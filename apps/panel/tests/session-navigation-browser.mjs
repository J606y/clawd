import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { withPage, waitFor } from "./fixtures/cdp-harness.mjs";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const artifact = process.env.CLAWD_TEST_ARTIFACTS;
const parentText = "主会话保留正文_MAIN_ONLY";
const childText = "子代理独立正文_CHILD_ONLY";
const init = `window.__gate=${JSON.stringify({mode:"chat",sessionId:"main",sessions:[{id:"main",title:"主会话",source:"api_server"},{id:"child",title:"子会话",source:"subagent",parent_session_id:"main"}],messages:{main:[{id:1,session_id:"main",role:"user",content:"请处理主任务"},{id:2,session_id:"main",role:"assistant",content:parentText}],child:[{id:3,session_id:"child",role:"assistant",content:childText}]}})};`;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
for (const mobile of [false, true]) {
  await withPage(root, "/tests/fixtures/session-navigation.html", async ({call,evaluate,sessionId,logs}) => {
    await call("Emulation.setDeviceMetricsOverride", {width:mobile?390:1280,height:mobile?844:800,deviceScaleFactor:1,mobile},sessionId);
    const body = () => evaluate("document.querySelector('#chat-slot')?.innerText || ''");
    const ready = async (id,text) => {
      await waitFor(evaluate,`window.fixtureReady && window.__selected?.() === ${JSON.stringify(id)} && document.querySelector('#chat-slot')?.innerText.includes(${JSON.stringify(text)})`,`${id} visible owner`,15000);
      assert.equal(await evaluate("window.fixtureError || ''"),"");
    };
    const select = async id => {
      const point = await evaluate(`(() => { const el=document.querySelector('[data-test-session="${id}"]'); const r=el.getBoundingClientRect(); const x=r.x+r.width/2,y=r.y+r.height/2; if(!el.contains(document.elementFromPoint(x,y))) throw Error('navigation button not hit'); return {x,y}; })()`);
      if(mobile){await call("Input.dispatchTouchEvent",{type:"touchStart",touchPoints:[point]},sessionId);await call("Input.dispatchTouchEvent",{type:"touchEnd",touchPoints:[]},sessionId);}
      else {await call("Input.dispatchMouseEvent",{type:"mousePressed",...point,button:"left",clickCount:1},sessionId);await call("Input.dispatchMouseEvent",{type:"mouseReleased",...point,button:"left",clickCount:1},sessionId);}
    };
    const screenshot = async name => {
      if(!artifact) return;
      await mkdir(artifact,{recursive:true});
      const shot=await call("Page.captureScreenshot",{format:"png",captureBeyondViewport:false},sessionId);
      await writeFile(path.join(artifact,`${mobile?'mobile':'desktop'}-${name}.png`),Buffer.from(shot.data,"base64"));
    };
    await ready("main",parentText);assert.equal((await body()).includes(childText),false);
    await select("child");await ready("child",childText);assert.equal((await body()).includes(parentText),false);await screenshot("child");
    const history=await call("Page.getNavigationHistory",{},sessionId);
    assert.ok(history.currentIndex>0);assert.ok(history.entries[history.currentIndex-1].url.endsWith("#main"));
    await call("Page.navigateToHistoryEntry",{entryId:history.entries[history.currentIndex-1].id},sessionId);
    await ready("main",parentText);assert.equal((await body()).includes(childText),false);
    const oldEpoch=await evaluate("window.__fixtureEpoch");
    await call("Page.reload",{},sessionId);
    await waitFor(evaluate,`window.__fixtureEpoch && window.__fixtureEpoch !== ${JSON.stringify(oldEpoch)} && window.fixtureReady`,"new document epoch",15000);
    await ready("main",parentText);
    await evaluate("window.__heldMessages=null;window.__holdMessages=true;void window.__reload();true");
    await waitFor(evaluate,"Boolean(window.__heldMessages)","held parent history");
    await evaluate("window.__holdMessages=false;true");await select("child");await ready("child",childText);
    await evaluate(`window.__heldMessages.resolve(200,{session_id:'main',data:[{id:90,session_id:'main',role:'assistant',content:'LATE_MAIN_RESPONSE'}],pagination:{returned:1,limit:100,offset:0}});true`);
    await sleep(350);assert.equal(await evaluate("window.__selected()"),"child");assert.equal((await body()).includes("LATE_MAIN_RESPONSE"),false);assert.equal((await body()).includes(parentText),false);
    await select("main");await ready("main",parentText);
    await evaluate("window.__heldMessages=null;window.__holdMessages=true;void window.__reload();true");
    await waitFor(evaluate,"Boolean(window.__heldMessages)","held same-session reload");
    await evaluate(`window.__holdMessages=false;window.__heldMessages.resolve(200,{session_id:'child',data:[{id:91,session_id:'child',role:'assistant',content:${JSON.stringify(childText)}}],pagination:{returned:1,limit:100,offset:0}});true`);
    await sleep(350);assert.equal(await evaluate("window.__selected()"),"main");assert.ok((await body()).includes(parentText));assert.equal((await body()).includes(childText),false);
    await screenshot("main");
    assert.equal(await evaluate("window.fixtureError || ''"),"");
    const unexpected=logs.filter(line=>/ReferenceError|TypeError|Unhandled|uncaught/i.test(line));assert.deepEqual(unexpected,[]);
    console.log(JSON.stringify({case:mobile?"mobile":"desktop",passed:true,checks:["open-main","open-child","history-back","fresh-document-reload","late-main-response-isolated","foreign-child-response-keeps-owned-history"],layer:"actual ChatPage with synthetic transport and test-only navigation; not production Shell/login E2E"}));
  },init);
}
console.log("session navigation browser PASS");
