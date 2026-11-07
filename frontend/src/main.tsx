import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import mainTemplate from '../../site/index.html?raw';
import sreTemplate from '../../site/sre-studio.html?raw';
import groupATemplate from '../../intelligence/group_a/site/group-a.html?raw';

type Surface = 'main' | 'sre' | 'group-a';

interface LegacyDocument {
  body: string;
  title: string;
  className: string;
  scripts: string[];
  styles: string[];
}

const templates: Record<Surface, string> = {
  main: mainTemplate,
  sre: sreTemplate,
  'group-a': groupATemplate,
};

function route(): Surface {
  if (location.pathname === '/sre-studio.html') return 'sre';
  if (location.pathname === '/group-a.html') return 'group-a';
  return 'main';
}

function parseTemplate(raw: string): LegacyDocument {
  const doc = new DOMParser().parseFromString(raw, 'text/html');
  doc.body.querySelectorAll('script').forEach((script) => script.remove());

  const scripts = Array.from(doc.querySelectorAll('head script[src]'))
    .map((element) => element.getAttribute('src') || '');
  const styles = Array.from(doc.querySelectorAll('head link[rel="stylesheet"]'))
    .map((element) => element.getAttribute('href') || '');

  if (scripts.some((src) => !/^\/[a-z0-9-]+\.js$/.test(src))) {
    throw new Error('Недопустимый путь браузерного модуля.');
  }
  if (styles.some((href) => !/^\/[a-z0-9-]+\.css$/.test(href))) {
    throw new Error('Недопустимый путь стилей.');
  }

  return {
    body: doc.body.innerHTML,
    title: doc.title,
    className: doc.body.className,
    scripts,
    styles,
  };
}

const surface = route();
const initialTemplate = parseTemplate(templates[surface]);
document.documentElement.lang = 'ru';
document.body.className = initialTemplate.className;
document.title = initialTemplate.title;

// Link tags are created before React mounts; existing selectors and designs
// remain unchanged while individual feature panels move to native components.
for (const href of initialTemplate.styles) {
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = href;
  document.head.append(link);
}

function loadClassicScript(src: string): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = '/react-legacy' + src;
    script.async = false;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('Не удалось загрузить модуль ' + src));
    document.head.append(script);
  });
}

function App() {
  const template = useMemo(() => initialTemplate, []);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function start() {
      for (const src of template.scripts) {
        if (cancelled) return;
        await loadClassicScript(src);
      }
    }
    void start().catch((reason: unknown) => {
      if (!cancelled) {
        setError(reason instanceof Error ? reason.message : 'Ошибка загрузки интерфейса.');
      }
    });
    return () => { cancelled = true; };
  }, [template]);

  return (
    <>
      {/*
        React owns the document surface. Feature controllers are served from
        TypeScript and continue using established DOM IDs, events, and API
        contracts until each feature is replaced by stateful React components.
        Do not rerender this DOM island: controllers mutate its descendants.
      */}
      <div data-cm-surface={surface} dangerouslySetInnerHTML={{ __html: template.body }} />
      {error && (
        <div role="alert" style={{
          position: 'fixed', bottom: 16, right: 16, zIndex: 9999,
          padding: 16, maxWidth: 440, background: '#7f1d1d', color: '#fff',
          borderRadius: 8,
        }}>
          {error}
        </div>
      )}
    </>
  );
}

const mount = document.getElementById('root');
if (!mount) throw new Error('React root is missing.');
createRoot(mount).render(<App />);
