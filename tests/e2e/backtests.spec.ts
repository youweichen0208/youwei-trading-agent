import {readFileSync,mkdirSync} from "node:fs";
import {test,expect,type Page,type BrowserContext} from "@playwright/test";
const fixture=(name:string)=>JSON.parse(readFileSync(`packages/contracts/fixtures/${name}.json`,"utf8"));
const catalog=fixture("backtest-page"),complete=fixture("backtest-report"),incomplete=fixture("backtest-report-incomplete");
let cookies:Awaited<ReturnType<BrowserContext["cookies"]>>=[];
async function login(page:Page,enabled=true){
  if(cookies.length)await page.context().addCookies(cookies);
  else {await page.goto("/login");await page.getByLabel("邮箱").fill(process.env.YTA_E2E_EMAIL!);await page.getByLabel("密码").fill(process.env.YTA_E2E_PASSWORD!);await page.getByRole("button",{name:"进入研究工作台"}).click();await expect(page).not.toHaveURL(/\/login$/);cookies=await page.context().cookies();}
  if(enabled)await page.route("**/api/yta/v1/me",async route=>{const response=await route.fetch();expect(response.status()).toBe(200);await route.fulfill({json:{...await response.json(),experimental_models_enabled:true}});});
}
test("backtests stay unavailable by default without issuing report requests",async({page})=>{
  await login(page,false);let calls=0;page.on("request",r=>{if(r.url().includes("/api/yta/v1/backtest-reports"))calls++;});
  await page.goto("/backtests");await expect(page.getByRole("heading",{name:"回测报告尚未启用"})).toBeVisible();
  await page.goto("/backtests/"+complete.id);await expect(page.getByRole("heading",{name:"回测报告尚未启用"})).toBeVisible();expect(calls).toBe(0);
});
test("catalog paginates, resets status cursor, scopes model and opens fixed identity",async({page})=>{
  await login(page);const urls:URL[]=[];
  await page.route("**/api/yta/v1/backtest-reports?*",route=>{
    const url=new URL(route.request().url());urls.push(url);
    const filtered=url.searchParams.get("status");
    return route.fulfill({json:filtered?{items:[],next_cursor:null}:url.searchParams.has("cursor")?{items:[catalog.items[1]],next_cursor:null}:{items:[catalog.items[0]],next_cursor:catalog.items[0].id}});
  });
  await page.route("**/api/yta/v1/backtest-reports/"+catalog.items[0].id,route=>route.fulfill({json:catalog.items[0].id===complete.id?complete:incomplete}));
  const model=catalog.items[0].model_ids[0];await page.goto("/backtests?model_id="+model);
  await expect(page.getByRole("article")).toHaveCount(1);expect(urls.at(-1)?.searchParams.get("model_id")).toBe(model);
  await page.getByRole("button",{name:"下一页",exact:true}).click();await expect(page.getByText("第 2 页")).toBeVisible();
  await expect(page.getByRole("article")).toContainText(catalog.items[1].content_hash.slice(0,12));expect(urls.at(-1)?.searchParams.get("cursor")).toBe(catalog.items[0].id);
  await page.getByLabel("报告完整性").selectOption("INCOMPLETE");await expect(page.getByRole("heading",{name:"暂无匹配的回测报告"})).toBeVisible();
  expect(urls.at(-1)?.searchParams.has("cursor")).toBe(false);
  await page.getByRole("button",{name:"查看全部完整性状态"}).click();await expect(page.getByRole("article")).toHaveCount(1);
  await page.getByRole("link",{name:"重放并查看报告 →"}).click();await expect(page).toHaveURL(new RegExp('/backtests/'+catalog.items[0].id+'$'));
  await expect(page.getByRole("heading",{name:"逐笔执行与退出权益"})).toBeVisible();
});
test("complete detail preserves costs, excluded signals and exact identities on desktop and mobile",async({page})=>{
  await login(page);await page.route("**/api/yta/v1/backtest-reports/"+complete.id,route=>route.fulfill({json:complete}));
  await page.setViewportSize({width:1440,height:1000});await page.goto("/backtests/"+complete.id);
  await expect(page.getByRole("heading",{name:"执行结果可计算，尚未取得正式资格"})).toBeVisible();
  await expect(page.getByText(complete.report.strategy_net_return,{exact:true}).first()).toBeVisible();
  await expect(page.getByRole("region",{name:"回测逐笔执行"})).toContainText("重叠排除");
  await page.getByText(`信号 ${complete.report.rows[0].signal_id.slice(0,8)}：已计算 · 查看执行依据`,{exact:true}).click();
  await expect(page.locator("details[open]").getByText("实际模型登记（美东）",{exact:true})).toBeVisible();
  mkdirSync(".local/backtest-ui",{recursive:true});await page.screenshot({path:".local/backtest-ui/complete-desktop.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});await expect(page.getByText(complete.id,{exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:".local/backtest-ui/complete-mobile.png",fullPage:true});
  await page.reload();await expect(page.getByRole("heading",{name:"执行结果可计算，尚未取得正式资格"})).toBeVisible();
});
test("incomplete report keeps final metrics unavailable and exposes missing execution evidence",async({page})=>{
  await login(page);await page.route("**/api/yta/v1/backtest-reports/"+incomplete.id,route=>route.fulfill({json:incomplete}));
  await page.goto("/backtests/"+incomplete.id);await expect(page.getByRole("heading",{name:"证据不足，无法计算完整收益"})).toBeVisible();
  const coverage=page.locator("section").filter({has:page.getByRole("heading",{name:"覆盖范围"})});
  await expect(coverage.locator(".model-metrics dd")).toHaveText(["无法计算","无法计算","无法计算"]);
  await expect(page.getByRole("region",{name:"回测逐笔执行"})).toContainText("未知");
  const row=incomplete.report.rows.find((r:{status:string})=>r.status==="EVIDENCE_UNAVAILABLE");
  await page.getByText(`信号 ${row.signal_id.slice(0,8)}：证据不足 · 查看执行依据`,{exact:true}).click();
  await expect(page.getByText("缺少所需交易日行情",{exact:false})).toBeVisible();
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:".local/backtest-ui/incomplete-mobile.png",fullPage:true});
});
test("detail refuses wrong identity and unavailable replay, then retries the same report",async({page})=>{
  await login(page);let attempt=0;const paths:string[]=[];
  await page.route("**/api/yta/v1/backtest-reports/"+complete.id,route=>{
    paths.push(route.request().url());attempt++;
    return attempt===1?route.fulfill({status:503,json:{error:{code:"TRAINING_UNAVAILABLE",message:"private internal explanation"}}}):route.fulfill({json:attempt===2?incomplete:complete});
  });
  await page.goto("/backtests/"+complete.id);await expect(page.getByRole("main").getByRole("alert")).toContainText("暂时无法重放");
  await expect(page.getByText("private internal explanation")).toHaveCount(0);
  await page.getByRole("button",{name:"重新加载"}).click();await expect(page.getByRole("main").getByRole("alert")).toContainText("暂时无法重放");
  await expect(page.getByRole("heading",{name:"覆盖范围"})).toHaveCount(0);
  await page.getByRole("button",{name:"重新加载"}).click();await expect(page.getByRole("heading",{name:"覆盖范围"})).toBeVisible();expect(new Set(paths).size).toBe(1);
});
test("old catalog results cannot replace a new filter and wrong mode stays hidden",async({page})=>{
  await login(page);let release!:()=>void,started!:()=>void;const wait=new Promise<void>(r=>release=r),ready=new Promise<void>(r=>started=r);
  await page.route("**/api/yta/v1/backtest-reports?*",async route=>{
    const status=new URL(route.request().url()).searchParams.get("status");
    if(!status){started();await wait;}
    await route.fulfill({json:status?{items:[],next_cursor:null}:catalog}).catch(()=>{});
  });
  await page.goto("/backtests");await ready;await page.getByLabel("报告完整性").selectOption("COMPLETE");await expect(page.getByRole("heading",{name:"暂无匹配的回测报告"})).toBeVisible();release();await expect(page.getByRole("article")).toHaveCount(0);
  await page.unroute("**/api/yta/v1/backtest-reports?*");await page.route("**/api/yta/v1/backtest-reports?*",route=>route.fulfill({json:{...catalog,items:catalog.items.map((r:object)=>({...r,data_mode:"LIVE_RESEARCH"}))}}));
  await page.reload();await expect(page.getByRole("main").getByRole("alert")).toContainText("回测目录暂时无法读取");await expect(page.getByRole("article")).toHaveCount(0);
});
