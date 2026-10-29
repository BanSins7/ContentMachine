'use strict';
// Display context only: no connection, API request or write permission is inferred.
(() => {
  const {stages,projectId}=window.ContentMachineWorkspaceCore;
  for(const stage of stages){
    const a=document.createElement('a');a.href='/#stage/'+stage.id;a.textContent=stage.number;
    a.title=stage.name;a.setAttribute('aria-label',stage.name);
    if(stage.id==='sre')a.setAttribute('aria-current','step');
    document.getElementById('srePipelineNav').append(a);
  }
  const pid=new URLSearchParams(location.search).get('project');
  if(projectId(pid))document.getElementById('sreProject').value=pid;
})();
