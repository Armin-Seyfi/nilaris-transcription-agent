// Offline event-wiring verification against the actual template, without a browser or API.
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const html=readFileSync(fileURLToPath(new URL('../src/templates/index.html',import.meta.url)),'utf8');
const ids=new Set([...html.matchAll(/id="([^"]+)"/g)].map(match=>match[1]));
assert(!ids.has('downloadWordFaButton'));assert(!ids.has('downloadWordEnButton'));
const elements=new Map();
for(const id of ids)elements.set(id,{textContent:'',disabled:false,hidden:false,open:false,handlers:{},
    addEventListener(name,callback){this.handlers[name]=callback;},showModal(){this.open=true;}});
const dialog=elements.get('transcriptDialog');
let state='not_generated', generationCalls=0;
const context=vm.createContext({document:{getElementById(id){assert(ids.has(id),`Missing element ${id}`);return elements.get(id);}},
    transcriptDialog:dialog,transcriptSession:elements.get('transcriptSession'),transcriptContent:elements.get('transcriptContent'),
    window:{location:{href:''}},setTimeout(){return 1;},clearTimeout(){},
    async fetch(url,options={}){
        if(url.endsWith('/transcript'))return {ok:true,json:async()=>({transcript:'Original transcript unchanged'})};
        if(url.endsWith('/notes/generate')){assert.equal(options.method,'POST');generationCalls++;state='ready';}
        return {ok:true,json:async()=>({state,error:state==='error'?'Simulated failure':''})};
    }});
const code=html.slice(html.indexOf('        const transcriptDownloads ='),html.indexOf('        const processingDialog ='));
vm.runInContext(code,context);
await vm.runInContext('viewTranscript("nilaris_2026-10-13T12-00-00Z")',context);
await new Promise(resolve=>setImmediate(resolve));
const button=elements.get('notesButton');
assert.equal(button.textContent,'Generate Meeting Notes with AI');assert.equal(button.disabled,false);
await button.onclick();assert.equal(generationCalls,1);
assert.equal(button.textContent,'Download AI-Generated Notes (Word)');
await button.onclick();assert.equal(generationCalls,1);
assert(context.window.location.href.endsWith('/download/notes'));
elements.get('downloadTxtButton').onclick();assert(context.window.location.href.endsWith('/download/txt'));
state='generating';await vm.runInContext('updateNotes(activeTranscriptSessionId, notesViewVersion)',context);
assert.equal(button.disabled,true);assert.equal(button.textContent,'Generating AI Notes…');
state='error';await vm.runInContext('updateNotes(activeTranscriptSessionId, notesViewVersion)',context);
assert.equal(button.disabled,false);assert.equal(button.textContent,'Retry Meeting Notes with AI');
assert.equal(elements.get('notesStatus').textContent,'Simulated failure');
dialog.handlers.close();
console.log('UI wiring verified: generate, cached Word download, raw TXT, progress and explicit retry.');
