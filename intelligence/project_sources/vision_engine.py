"""Stage 08: bounded local FFmpeg visual sampling and image-only Sonnet 4.6 adapter.

No media requests/LLM calls at import. Frame observations are sparse point samples,
not complete-video claims. Model statements remain unverified until review.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

VERSION='vision-prepare/v1'
OBS_VERSION='visual-observations/v1'
MODEL='anthropic/claude-sonnet-4.6'
PROMPT_VERSION='vision-frames/v1'
MAX_FRAMES=8
MAX_FRAME_BYTES=180_000
MAX_REQUEST_BYTES=2_000_000
MAX_PROVIDER_BYTES=600_000
MAX_CLIP_SECONDS=900
VISION_ESTIMATE_USD='0.15'  # planning allowance, not a provider hard cap

class VisionProblem(Exception):
    def __init__(self, message, code='vision_error', http_status=422):
        super().__init__(message)
        self.code=code;self.http_status=http_status


def canonical(data):
    return json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')

def sha(data):return hashlib.sha256(data).hexdigest()


def settings(body):
    if not isinstance(body,dict) or set(body)-{'max_frames','early_focus','ocr'}:
        raise VisionProblem('Недопустимая конфигурация кадров.','invalid_config',400)
    frames=body.get('max_frames',MAX_FRAMES)
    if type(frames) is not int or not 2<=frames<=MAX_FRAMES:
        raise VisionProblem('max_frames: от 2 до 8.','invalid_config',400)
    early=body.get('early_focus',True)
    ocr=body.get('ocr',True)
    if type(early) is not bool or type(ocr) is not bool:
        raise VisionProblem('early_focus и ocr должны быть boolean.','invalid_config',400)
    return {'max_frames':frames,'early_focus':early,'ocr':ocr}


def ffmpeg_bin():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError,RuntimeError) as exc:
        raise VisionProblem('FFmpeg недоступен.','ffmpeg_unavailable',503) from exc


def probe(path,*,run=subprocess.run):
    """Use ffprobe if available; fallback to FFmpeg metadata output on Windows."""
    if shutil.which('ffprobe'):
        cmd=['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]
        try:
            p=run(cmd,capture_output=True,timeout=18,check=False)
            if p.returncode==0:
                obj=json.loads(p.stdout)
                streams=obj.get('streams',[]);video=next((s for s in streams if s.get('codec_type')=='video'),None)
                if video:
                    duration=float(video.get('duration') or obj.get('format',{}).get('duration') or 0)
                    if 0<duration<=86400:
                        return {'duration_ms':round(duration*1000),'codec':str(video.get('codec_name') or 'unknown')[:60],
                                'width':int(video.get('width') or 0),'height':int(video.get('height') or 0),
                                'frame_count':int(video.get('nb_frames') or 0) or None,
                                'audio_present':any(s.get('codec_type')=='audio' for s in streams),
                                'probe_engine':'ffprobe'}
        except (ValueError,TypeError,subprocess.TimeoutExpired,OSError,json.JSONDecodeError):pass
    try:
        result=run([ffmpeg_bin(),'-hide_banner','-i',str(path)],capture_output=True,timeout=18,check=False)
        diagnostic=(result.stderr or b'').decode('utf-8',errors='replace')
        m=re.search(r'Duration:\s*(\d+):(\d+):([0-9.]+)',diagnostic)
        vid=re.search(r'Stream\s+#.*?:\s+Video:\s*([^, ]+)',diagnostic)
        xy=re.search(r'\b(\d{2,5})x(\d{2,5})\b',diagnostic)
        if not m or not vid:
            raise VisionProblem('Не удалось определить свойства видео.','probe_failed',422)
        seconds=int(m[1])*3600+int(m[2])*60+float(m[3])
        if not 0<seconds<=86400:raise VisionProblem('Некорректная длительность видео.','probe_failed',422)
        return {'duration_ms':round(seconds*1000),'codec':vid[1][:60],
                'width':int(xy[1]) if xy else 0,'height':int(xy[2]) if xy else 0,
                'frame_count':None,
                'audio_present':bool(re.search(r'Stream\s+#.*?:\s+Audio:',diagnostic)),
                'probe_engine':'ffmpeg_fallback'}
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise VisionProblem('Не удалось запустить FFmpeg.','probe_failed',503) from exc


def scene_cuts(path,duration_ms,*,run=subprocess.run):
    """Sparse 2 fps change detector, bounded to 120 seconds of input time.

    Cut timestamps from FFmpeg showinfo; unsupported codecs return no cuts,
    never fabricated scene boundaries.
    """
    seconds=min(duration_ms/1000,120)
    if seconds<=1:return []
    cmd=[ffmpeg_bin(),'-hide_banner','-nostdin','-loglevel','info','-t',str(round(seconds,2)),
         '-i',str(path),'-an','-vf','fps=2,select=gt(scene\\,0.33),showinfo',
         '-f','null','-']
    try:
        r=run(cmd,capture_output=True,timeout=45,check=False)
        err=(r.stderr or b'')[:1_000_000].decode('utf-8',errors='replace')
        if r.returncode!=0:return []
        found=sorted(set(int(float(v)*1000) for v in re.findall(r'pts_time:([0-9.]+)',err)))
        return [x for x in found if 250<x<duration_ms-250][:100]
    except (OSError,ValueError,subprocess.TimeoutExpired):return []


def sampling(duration_ms,cuts,config):
    """Bounded early-hook + uniform temporal coverage + shot changes."""
    if not isinstance(duration_ms,int) or duration_ms<=0:
        raise VisionProblem('Некорректная длительность.','invalid_duration')
    n=config['max_frames'];last=max(0,duration_ms-100)
    # Avoid overstating full-time coverage: these are isolated sampled instants.
    early=[200,900,2000,4000,6500] if config['early_focus'] else []
    base=[round((i+0.5)*duration_ms/n) for i in range(n)]
    candidates=[]
    for t in early[:min(3,n//2)]+base:
        t=max(0,min(last,t))
        if all(abs(t-v)>max(150,duration_ms//(n*18)) for v in candidates):candidates.append(t)
    for t in cuts:
        if len(candidates)>=n*3:break
        t=max(0,min(last,int(t)))
        if all(abs(t-v)>350 for v in candidates):candidates.append(t)
    priority=sorted(set(max(0,min(last,int(x))) for x in candidates))
    # When there are too many samples, ensure early and long-range coverage;
    # give cuts priority only if they occupy a distinct temporal bin.
    bins=[(i*duration_ms/n,(i+1)*duration_ms/n) for i in range(n)]
    picks=[]
    for i,(lo,hi) in enumerate(bins):
        c=[t for t in priority if lo<=t<hi]
        cut=[t for t in c if any(abs(t-z)<150 for z in cuts)]
        target=lo+(hi-lo)*(0.2 if config['early_focus'] and i==0 else 0.5)
        t=min(cut or c,key=lambda v:abs(v-target)) if c else round(target)
        picks.append(max(0,min(last,t)))
    return sorted(set(picks))[:n]


def frame_jpeg(video,time_ms,*,run=subprocess.run):
    cmd=[ffmpeg_bin(),'-hide_banner','-nostdin','-loglevel','error','-ss',f'{time_ms/1000:.3f}',
         '-i',str(video),'-an','-frames:v','1','-vf',"scale='min(800,iw)':-2",
         '-q:v','5','-f','image2pipe','-vcodec','mjpeg','pipe:1']
    try:r=run(cmd,capture_output=True,timeout=14,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise VisionProblem('Ошибка извлечения кадра.','frame_failed') from exc
    data=r.stdout or b''
    if r.returncode or len(data)<20 or len(data)>MAX_FRAME_BYTES or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        raise VisionProblem('Некорректный или слишком большой JPEG кадр.','frame_failed')
    return data


def ocr_jpeg(jpeg,*,tesseract=None):
    if tesseract is None:
        if not shutil.which('tesseract'):
            return {'status':'unavailable','text':'','engine':None,'error':'tesseract_missing'}
        import pytesseract
        from PIL import Image
        tesseract=lambda img: pytesseract.image_to_string(img,lang='eng',timeout=6)
    try:
        from PIL import Image
        with Image.open(io.BytesIO(jpeg)) as image:
            image.verify()
        with Image.open(io.BytesIO(jpeg)) as image:
            s=tesseract(image)
        text=' '.join(str(s).split())[:2000]
        return {'status':'ok' if text else 'no_text_detected','text':text,'engine':'tesseract',
                'error':None,'language_hint':'eng'}
    except Exception:
        return {'status':'failed','text':'','engine':'tesseract','error':'ocr_failed'}


def prepare(video,config,write_frame,*,prober=probe,cut_finder=scene_cuts,extractor=frame_jpeg,ocr=ocr_jpeg):
    """write_frame(frame_id, jpg)->relative path; caller owns durable storage."""
    config=settings(config)
    meta=prober(video)
    duration=meta['duration_ms']
    if duration<=0:raise VisionProblem('Видео без длительности.','invalid_media')
    cuts=cut_finder(video,duration)
    times=sampling(duration,cuts,config)
    scenes=[]
    boundaries=[0]+[x for x in cuts if 0<x<duration]+[duration]
    # For a long video with no cuts detect broad bins, mark coarse not exact shot cuts.
    if len(boundaries)==2 and duration>15000:
        boundaries=[0]+[round(duration*i/4) for i in range(1,4)]+[duration]
    boundaries=sorted(set(boundaries))
    for i,(a,b) in enumerate(zip(boundaries,boundaries[1:])):
        scenes.append({'scene_id':f'sc{i+1:03d}','start_ms':a,'end_ms':b,
                       'boundary_kind':'detected_cut' if cuts else 'sampling_bin',
                       'frame_ids':[]})
    frames=[];issues=[]
    for idx,t in enumerate(times):
        try:jpeg=extractor(video,t)
        except VisionProblem:
            issues.append({'frame_time_ms':t,'reason':'frame_failed'});continue
        fid=f'f{idx+1:03d}'
        ref=write_frame(fid,jpeg)
        scene=next((s for s in scenes if s['start_ms']<=t<s['end_ms']),scenes[-1])
        scene['frame_ids'].append(fid)
        o=ocr(jpeg) if config['ocr'] else {'status':'skipped','text':'','engine':None,'error':None}
        # OCR interval is the physical sampled frame only; never infer text
        # persistence until the next sampled frame or over the whole scene.
        frame_span=max(1,min(40,duration-t))
        o['start_ms']=t;o['end_ms']=t+frame_span;o['coverage_kind']='single_frame'
        o['confidence']=None  # Tesseract string extraction is not calibrated
        frames.append({'frame_id':fid,'time_ms':t,'scene_id':scene['scene_id'],
                       'storage_ref':ref,'sha256':sha(jpeg),'byte_size':len(jpeg),
                       'ocr':o,'time_precision':'sample_only'})
    if not frames:raise VisionProblem('Не удалось извлечь ни одного кадра.','no_frames')
    # Cap stored scenes to sampled relevant intervals; don't imply full analysis.
    selected=[s for s in scenes if s['frame_ids']]
    warnings=[x['reason'] for x in issues]
    if duration>120000:warnings.append('cuts_scanned_first_120s_only')
    if any(f['ocr']['status']=='unavailable' for f in frames):warnings.append('ocr_unavailable')
    return {'schema_version':VERSION,'status':'partial' if issues or duration>120000 or any(f['ocr']['status'] in ('failed','unavailable') for f in frames) else 'completed',
            'probe':meta,'video_duration_ms':duration,'frame_sampling':{'method':'early_focus_uniform_scene_cut',
                'sampled_frames':len(frames),'requested_frames':config['max_frames'],
                'all_frames_analyzed':False,'cuts_scan_limit_ms':min(duration,120000),
                'sampled_instants_ms':[f['time_ms'] for f in frames]},
            'scenes':selected,'frames':frames,'warnings':sorted(set(warnings)),'engine_versions':{
                'frame':'ffmpeg','ocr':'tesseract_optional','schema':VERSION},'trust_level':'local_frame_sample'}


def _safe_text(value,limit=1200):
    return type(value) is str and 0<len(value)<=limit and all(ord(c)>=32 or c in '\n\t' for c in value)


def validate_observations(obj,prepared):
    """Reject invented frames/scene IDs/times. Visual inference is kept separate."""
    if not isinstance(obj,dict) or set(obj)!={'schema_version','scenes','limitations'} or obj['schema_version']!=OBS_VERSION:
        raise VisionProblem('Некорректная схема Vision ответа.','invalid_model_json')
    if not isinstance(obj['scenes'],list) or len(obj['scenes'])>len(prepared['scenes']) or not isinstance(obj['limitations'],list):
        raise VisionProblem('Некорректный список сцен.','invalid_model_json')
    if len(obj['limitations'])>12 or any(not _safe_text(x,500) for x in obj['limitations']):
        raise VisionProblem('Некорректные ограничения.','invalid_model_json')
    allowed={x['scene_id']:x for x in prepared['scenes']}
    seen=set()
    for scene in obj['scenes']:
        fields={'scene_id','start_ms','end_ms','frame_ids','visual_description','observed_visible',
                'actions','objects','people_observations','product_logo_visibility',
                'camera_editing','inferred_interpretation','uncertainty'}
        if not isinstance(scene,dict) or set(scene)!=fields:
            raise VisionProblem('Некорректная схема наблюдения.','invalid_model_json')
        sid=scene['scene_id']
        if sid in seen or sid not in allowed:raise VisionProblem('Выдуманная сцена.','invalid_model_reference')
        seen.add(sid);source=allowed[sid]
        if scene['start_ms']!=source['start_ms'] or scene['end_ms']!=source['end_ms']:
            raise VisionProblem('Изменены границы сцены.','invalid_model_reference')
        ids=scene['frame_ids']
        if not isinstance(ids,list) or not ids or len(ids)>MAX_FRAMES or len(set(ids))!=len(ids) or not set(ids).issubset(source['frame_ids']):
            raise VisionProblem('Выдуманные ссылки на кадры.','invalid_model_reference')
        if not _safe_text(scene['visual_description']):raise VisionProblem('Некорректное описание.','invalid_model_json')
        for key in ('observed_visible','actions','objects','people_observations','camera_editing'):
            arr=scene[key]
            if not isinstance(arr,list) or len(arr)>12 or any(not _safe_text(x,250) for x in arr):
                raise VisionProblem('Неверные списки наблюдений.','invalid_model_json')
        if scene['product_logo_visibility'] not in ('visible','not_visible','uncertain'):
            raise VisionProblem('Неверное наблюдение о логотипе.','invalid_model_json')
        for name in ('inferred_interpretation','uncertainty'):
            v=scene[name]
            if v is not None and not _safe_text(v,600):raise VisionProblem('Неверная неопределённость.','invalid_model_json')
    return {'schema_version':OBS_VERSION,'status':'partial' if len(seen)<len(allowed) else 'completed',
            'scenes':obj['scenes'],'limitations':obj['limitations'],
            'frame_sampling':prepared['frame_sampling'],'video_duration_ms':prepared['video_duration_ms'],
            'trust_level':'model_reported_unverified','vision_model':MODEL,'prompt_version':PROMPT_VERSION}


def provider_payload(prepared,frame_bytes):
    if len(frame_bytes)>MAX_FRAMES or not frame_bytes:raise VisionProblem('Неверное количество кадров.','invalid_frames')
    index={f['frame_id']:f for f in prepared['frames']}
    content=[{'type':'text','text':(
        'Analyze only these sampled images. No audio/transcript supplied. Do not identify people, invent events between frames, '
        'claim full-video coverage, infer causes of popularity or assert AI-origin. Return strict JSON schema: '
        '{"schema_version":"visual-observations/v1","scenes":[{"scene_id":str,"start_ms":int,"end_ms":int,'
        '"frame_ids":[str],"visual_description":str,"observed_visible":[str],"actions":[str],"objects":[str],'
        '"people_observations":[str],"product_logo_visibility":"visible|not_visible|uncertain",'
        '"camera_editing":[str],"inferred_interpretation":str|null,"uncertainty":str|null}],"limitations":[str]}. '
        'Use exact scene_id/start_ms/end_ms from metadata, only given frame_ids for evidence. '
        'Describe visible facts separately from uncertain interpretations. Limits: 8 scenes max. '\
        +json.dumps({'scenes':prepared['scenes'],'times':[{'frame_id':f['frame_id'],'time_ms':f['time_ms']} for f in prepared['frames']]},ensure_ascii=False))}]
    for fid,jpeg in frame_bytes:
        if fid not in index or sha(jpeg)!=index[fid]['sha256'] or len(jpeg)>MAX_FRAME_BYTES:
            raise VisionProblem('SHA кадра не совпадает.','frame_integrity')
        content.append({'type':'text','text':f'Frame {fid}, observed at {index[fid]["time_ms"]} ms.'})
        content.append({'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(jpeg).decode('ascii')}})
    result={'model':MODEL,'messages':[{'role':'user','content':content}], 'temperature':0,
            'max_tokens':2200,'response_format':{'type':'json_object'}}
    if len(canonical(result))>MAX_REQUEST_BYTES:raise VisionProblem('Слишком большой запрос Vision.','too_many_bytes')
    return result


def openrouter_vision(prepared,frame_bytes,key,*,opener=urlopen):
    body=canonical(provider_payload(prepared,frame_bytes))
    req=Request('https://openrouter.ai/api/v1/chat/completions',data=body,method='POST',headers={
        'Authorization':'Bearer '+key,'Content-Type':'application/json','X-Title':'ContentMachine PSI Vision'})
    try:
        with opener(req,timeout=105) as r:raw=r.read(MAX_PROVIDER_BYTES+1)
    except (HTTPError,URLError,TimeoutError,OSError) as exc:
        raise VisionProblem('OpenRouter vision недоступен; итог оплаты может быть неизвестен.','provider_uncertain',503) from exc
    if len(raw)>MAX_PROVIDER_BYTES:raise VisionProblem('Vision ответ превышает лимит.','provider_uncertain',503)
    try:
        obj=json.loads(raw)
        message=obj['choices'][0]['message']['content']
        if isinstance(message,list):message=''.join(x.get('text','') for x in message if isinstance(x,dict))
        if not isinstance(message,str):raise ValueError
        answer=json.loads(message)
    except (ValueError,KeyError,IndexError,TypeError) as exc:
        raise VisionProblem('Некорректный JSON от Vision модели.','invalid_model_json',422) from exc
    verified=validate_observations(answer,prepared)
    usage=obj.get('usage') if isinstance(obj.get('usage'),dict) else {}
    cost=usage.get('cost')
    try:cost=float(cost) if cost is not None else None
    except (ValueError,TypeError):cost=None
    if cost is not None and not 0<=cost<=100:cost=None
    return {'observations':verified,'provider_request_id':str(obj.get('id') or '')[:120],
            'usage':{'prompt_tokens':usage.get('prompt_tokens'),'completion_tokens':usage.get('completion_tokens'),
                     'cost_usd':cost}}
