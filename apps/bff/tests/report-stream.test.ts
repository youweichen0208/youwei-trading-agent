import {expect,it} from 'vitest';
import {reportStreamSummary} from '../../web/lib/report-stream.js';
it('progressively displays only top-level summary across every character boundary',()=>{
 const summary='合成回放："谨慎"\\观察\n不保证结果 😀。';
 const input=JSON.stringify({schema_version:'research-report-v1',evidence:[{summary:'hidden',tool_call_id:'private-reference'}],summary,risks:['later']});
 let previous='';
 for(let i=0;i<=input.length;i++){
  const text=reportStreamSummary(input.slice(0,i));
  expect(summary.startsWith(text)).toBe(true);expect(text.startsWith(previous)).toBe(true);
  expect(text).not.toContain('schema_version');expect(text).not.toContain('private-reference');previous=text;
 }
 expect(previous).toBe(summary);
});
it('handles escaped Unicode and nested quoted delimiters without exposing metadata',()=>{
 const raw='{"nested":{"value":"},\\\"summary\\\":\\\"wrong"},"summary":"\\u4e2d\\u6587 \\ud83d\\ude00"}';
 for(let i=0;i<=raw.length;i++)expect('中文 😀'.startsWith(reportStreamSummary(raw.slice(0,i)))).toBe(true);
 expect(reportStreamSummary(raw)).toBe('中文 😀');
 expect(reportStreamSummary('{"sum\\u006dary":"ok"}')).toBe('ok');
});
it('fails closed on malformed/non-object input and incomplete metadata',()=>{
 for(const input of ['plain text','[]','```json\n{}','{"summary":5}','{"summary":"bad\\x"}',
  '{"field":invalid,"summary":"wrong"}','{"nested":{"summary":"wrong"}',
  '{"field":[1,],"summary":"wrong"}','{"summary":"line\nbreak"}','x'.repeat(1_000_001)]){
  expect(reportStreamSummary(input)).toBe('');
 }
});
