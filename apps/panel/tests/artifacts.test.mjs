import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
const server=await createServer({server:{middlewareMode:true},appType:'custom'});
const {Lexer}=await server.ssrLoadModule('marked');
const lexCalls=[];
const origLex=Lexer.lex;
Lexer.lex=function(src,opt){lexCalls.push(String(src));return origLex.call(this,src,opt);};
try {
 const {parseAssistantContent,artifactReference}=await server.ssrLoadModule('/src/lib/artifacts.ts');
 await test('explicit MEDIA paths produce encoded image and file download references',()=>{
  const p='/root/.hermes/images/测试 图片.png';
  const result=parseAssistantContent(`完成\nMEDIA:"${p}"`);
  assert.equal(result.artifacts.length,1);
  assert.equal(result.artifacts[0].name,'测试 图片.png');
  assert.equal(result.artifacts[0].download,`/panel-core/files/artifact?path=${encodeURIComponent(p)}`);
  assert.equal(result.artifacts[0].preview,`${result.artifacts[0].download}&preview=1`);
  assert.equal(result.text,'完成\n');
  assert.equal(artifactReference('/root/hermes-workspace/report.pdf').image,false);
 });
 await test('fenced and inline code artifact examples remain untouched',()=>{
  const text='`MEDIA:/root/.hermes/images/code.png`\n```text\nMEDIA:/root/.hermes/images/fence.png\n```\n~~~\nMEDIA:/root/.hermes/images/tilde.png\n~~~\n    MEDIA:/root/.hermes/images/indent.png';
  assert.deepEqual(parseAssistantContent(text),{text,artifacts:[]});
 });
 await test('markdown and bare output paths become attachment cards, not ordinary source mentions',()=>{
  const paths=['/root/hermes-workspace/图 表.png','/root/hermes-workspace/文 件.pdf','/root/hermes-workspace/data.csv','/root/.hermes/cron/output/结果.zip'];
  const text=`![图](<${paths[0]}>)\n[文件]("${paths[1]}")\n${paths[2]}\n<${paths[3]}>\n/root/hermes-workspace/src/main.ts`;
  const result=parseAssistantContent(text);
  assert.deepEqual(result.artifacts.map(a=>a.source),paths);
  assert.ok(result.text.includes('/root/hermes-workspace/src/main.ts'));
  assert.equal(parseAssistantContent('MEDIA:/root/hermes-workspace/output.custom').artifacts.length,1);
 });
 await test('plain document and data deliverables become cards without requiring MEDIA',()=>{
  const extensions=['txt','md','epub','rtf','json','xml','yaml','yml','tsv','geojson','gpx','kml','kmz','tgz','bz2','xz','rar','apk','ipa','html','htm','svg','tiff','mov','mkv','avi','3gp','ogg','opus','flac','m4a','m2a'];
  for(const ext of extensions) {
   const p=`/root/hermes-workspace/output.${ext}`;
   assert.equal(parseAssistantContent(p).artifacts[0]?.source,p,ext);
  }
  assert.equal(parseAssistantContent('/root/hermes-workspace/example.py').artifacts.length,0);
 });
 await test('inline data raster images receive preview and named downloads',()=>{
  const data='data:image/png;base64,iVBORw0KGgo=';
  for(const text of [data,`![生成图片](${data})`]) {
   const result=parseAssistantContent(text);
   assert.equal(result.artifacts.length,1);
   assert.equal(result.artifacts[0].preview,data);
   assert.equal(result.artifacts[0].download,data);
   assert.equal(result.artifacts[0].name,'生成图片.png');
  }
 });
 await test('structured assistant image_url survives final, commentary and in-flight history projection',async()=>{
  const {buildTurns,withoutInFlight,segments}=await server.ssrLoadModule('/src/lib/transcript.ts');
  const data='data:image/png;base64,iVBORw0KGgo=';
  const row={id:2,role:'assistant',content:[{type:'image_url',image_url:{url:data}}],timestamp:2};
  const rows=[{id:1,role:'user',content:'合成请求',timestamp:1},row];
  assert.deepEqual(buildTurns(rows)[1].images,[data]);
  assert.deepEqual(withoutInFlight(rows),rows);
  const turns=buildTurns([{...row,tool_calls:[{id:'tool',function:{name:'terminal',arguments:'{}'}}]}]);
  assert.deepEqual(segments(turns[0].steps)[0].images,[data]);
 });
 await test('attachment parser refuses secret filenames and unsafe URL or path escapes',()=>{
  for(const p of ['/etc/passwd','/root/.hermes/.env','/root/.hermes/state.db','/root/hermes-workspace/../.hermes/images/a.png','/root/hermes-workspace/%2e%2e/a.png','/root/hermes-workspace/config.yaml','/root/hermes-workspace/state.db-wal','/root/hermes-workspace/auth.json','/root/hermes-workspace/authorized_keys','/root/hermes-workspace/id_ecdsa','javascript:alert(1)','https://example.test/a.png','data:image/svg+xml;base64,PHN2Zz4=']) {
   assert.equal(artifactReference(p),null,p);
   assert.equal(parseAssistantContent(`MEDIA:${p}`).artifacts.length,0,p);
  }
 });
 await test('nested list, quote and table inline code never discover artifacts',()=>{
  const text='> `MEDIA:/root/.hermes/images/quote.png`\n\n- `MEDIA:/root/.hermes/images/list.png`\n\n| 示例 |\n| --- |\n| `MEDIA:/root/.hermes/images/table.png` |';
  assert.deepEqual(parseAssistantContent(text),{text,artifacts:[]});
 });
 await test('reference-style markdown attachment links and images preserve local paths',()=>{
  const text='[文档][doc]\n![图片][pic]\n\n[doc]: </root/hermes-workspace/测试 文档.md>\n[pic]: /root/.hermes/images/ref.png';
  const result=parseAssistantContent(text);
  assert.deepEqual(result.artifacts.map(a=>a.source),['/root/hermes-workspace/测试 文档.md','/root/.hermes/images/ref.png']);
  assert.ok(!result.text.includes('[文档][doc]'));
 });
 await test('bounded discovery deduplicates artifacts, stops at scan limit and caches small messages',()=>{
  const text='MEDIA:/root/.hermes/images/same.png\nMEDIA:/root/.hermes/images/same.png';
  assert.equal(parseAssistantContent(text).artifacts.length,1);
  assert.equal(parseAssistantContent(text),parseAssistantContent(text));
  const many=Array.from({length:50},(_,i)=>`MEDIA:/root/hermes-workspace/report-${i}.pdf`).join('\n');
  assert.equal(parseAssistantContent(many).artifacts.length,32);
  const large='正文'.repeat(1024*1024)+'\nMEDIA:/root/.hermes/images/outside.png';
  assert.deepEqual(parseAssistantContent(large),{text:large,artifacts:[]});
 });
 await test('single-segment pure append does not lex the complete prefix again', async () => {
  const prefix = 'Hello';
  const next = 'Hello world';
  lexCalls.length = 0;
  const first = parseAssistantContent(prefix, { cache: false });
  assert.ok(lexCalls.some((src) => src === prefix), 'spy must observe the initial full lex: ' + JSON.stringify(lexCalls));
  lexCalls.length = 0;
  const second = parseAssistantContent(next, { cache: false, priorSource: prefix, priorResult: first });
  const during = lexCalls.slice();
  assert.equal(during.some((src) => src.startsWith(prefix)), false, 'second append must not full-lex the complete prefix: ' + JSON.stringify(during));
  const once = parseAssistantContent(next, { cache: false });
  assert.deepEqual(second, once);
  assert.equal(second.text.includes('<script') || second.text.includes('<img'), false);
  lexCalls.length = 0;
  const third = parseAssistantContent(next + ' again', { cache: false, priorSource: next, priorResult: second });
  assert.equal(lexCalls.some((src) => src.startsWith(next)), false, 'later append must not full-lex the grown prefix: ' + JSON.stringify(lexCalls));
  assert.deepEqual(third, parseAssistantContent(next + ' again', { cache: false }));
  const unsafe = 'See <script>alert(1)</script> and MEDIA:/root/hermes-workspace/secret.png';
  assert.deepEqual(parseAssistantContent(prefix + unsafe, { cache: false, priorSource: prefix, priorResult: first }), parseAssistantContent(prefix + unsafe, { cache: false }));
 });
 await test('multiline nested markdown normalization cannot expose code examples as files',()=>{
  for(const text of ['> 旁白\n> `MEDIA:/root/.hermes/images/quoted-code.png`\n> 结束','- 旁白\n  `MEDIA:/root/.hermes/images/list-code.png`\n  结束']) {
   assert.deepEqual(parseAssistantContent(text),{text,artifacts:[]});
  }
 });
}finally{await server.close();}
