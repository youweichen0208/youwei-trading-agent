import {readFileSync,mkdirSync} from "node:fs";
import {test,expect,type Page,type BrowserContext} from "@playwright/test";
const fixture=(name:string)=>JSON.parse(readFileSync(`packages/contracts/fixtures/${name}.json`,"utf8"));
const catalog=fixture("training-evidence-page"),matched=fixture("training-evidence-matched"),mismatch=fixture("training-evidence-mismatch");
const modelPath="/models/"+matched.model_id+"/training-evidence";
let cookies:Awaited<ReturnType<BrowserContext["cookies"]>>=[];
async function login(page:Page,enabled=true){
  if(cookies.length)await page.context().addCookies(cookies);
  else {await page.goto("/login");await page.getByLabel("邮箱").fill(process.env.YTA_E2E_EMAIL!);await page.getByLabel("密码").fill(process.env.YTA_E2E_PASSWORD!);await page.getByRole("button",{name:"进入研究工作台"}).click();await expect(page).not.toHaveURL(/\/login$/);cookies=await page.context().cookies();}
  if(enabled)await page.route("**/api/yta/v1/me",async route=>{const response=await route.fetch();expect(response.status()).toBe(200);await route.fulfill({json:{...await response.json(),experimental_models_enabled:true}});});
}
test("training evidence is disabled without issuing catalog or detail requests",async({page})=>{
  await login(page,false);let calls=0;page.on("request",r=>{if(r.url().includes("/api/yta/v1/")&&r.url().includes("training-evidence"))calls++;});
  await page.goto(modelPath);await expect(page.getByRole("heading",{name:"训练证据尚未启用"})).toBeVisible();
  await page.goto("/training-evidence/"+matched.id);await expect(page.getByRole("heading",{name:"训练证据尚未启用"})).toBeVisible();expect(calls).toBe(0);
});
test("training catalog paginates and links fixed detail with stored-result wording",async({page})=>{
  await login(page);const cursors:(string|null)[]=[];
  await page.route("**/api/yta/v1/experimental-models/*/training-evidence?*",route=>{
    const cursor=new URL(route.request().url()).searchParams.get("cursor");cursors.push(cursor);
    return route.fulfill({json:cursor?{items:[catalog.items[1]],next_cursor:null}:{items:[catalog.items[0]],next_cursor:catalog.items[0].id}});
  });
  await page.route("**/api/yta/v1/training-evidence/*",route=>route.fulfill({json:route.request().url().endsWith(matched.id)?matched:mismatch}));
  await page.goto(modelPath);await expect(page.getByRole("article")).toHaveCount(1);
  await expect(page.getByRole("article")).toContainText("保存时");
  await page.getByRole("button",{name:"下一页",exact:true}).click();await expect(page.getByText("第 2 页")).toBeVisible();
  await expect(page.getByRole("article")).toContainText(catalog.items[1].content_hash.slice(0,12));expect(cursors.at(-1)).toBe(catalog.items[0].id);
  await page.getByRole("button",{name:"上一页",exact:true}).click();await expect(page.getByText("第 1 页")).toBeVisible();
  await page.getByRole("link",{name:"重新训练并查看详情 →"}).click();await expect(page).toHaveURL(new RegExp('/training-evidence/'+catalog.items[0].id+'$'));
  await expect(page.getByRole("heading",{name:"固定输入与模型绑定"})).toBeVisible();
});
test("training detail shows matched and mismatched fields, reload and mobile identities",async({page})=>{
  await login(page);
  for(const saved of [matched,mismatch]){
    await page.route("**/api/yta/v1/training-evidence/"+saved.id,route=>route.fulfill({json:saved}));
    await page.setViewportSize({width:1440,height:1000});await page.goto("/training-evidence/"+saved.id);
    await expect(page.getByRole("heading",{name:saved===matched?"本次重训与原模型全部一致":"本次重训复现了已登记的差异"})).toBeVisible();
    for(const field of saved.report.mismatched_fields)await expect(page.locator("li code").getByText(field,{exact:true})).toBeVisible();
    await expect(page.getByText(saved.input_hash,{exact:true})).toBeVisible();
    mkdirSync(".local/training-evidence-ui",{recursive:true});await page.screenshot({path:`.local/training-evidence-ui/${saved.report.status}-desktop.png`,fullPage:true});
    await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`.local/training-evidence-ui/${saved.report.status}-mobile.png`,fullPage:true});
    await page.reload();await expect(page.getByRole("heading",{name:"固定输入与模型绑定"})).toBeVisible();
  }
});
test("training detail fails closed then retries same identity without leaking errors",async({page})=>{
  await login(page);let attempt=0;const paths:string[]=[];
  await page.route("**/api/yta/v1/training-evidence/"+matched.id,route=>{
    paths.push(route.request().url());attempt++;
    return attempt===1?route.fulfill({status:503,json:{error:{code:"TRAINING_UNAVAILABLE",message:"private training inputs"}}}):route.fulfill({json:attempt===2?mismatch:matched});
  });
  await page.goto("/training-evidence/"+matched.id);
  await expect(page.getByRole("main").getByRole("alert")).toContainText("暂时无法重新训练");await expect(page.getByText("private training inputs")).toHaveCount(0);
  await page.getByRole("button",{name:"重新加载"}).click();await expect(page.getByRole("main").getByRole("alert")).toContainText("暂时无法重新训练");
  await expect(page.getByRole("heading",{name:"固定输入与模型绑定"})).toHaveCount(0);
  await page.getByRole("button",{name:"重新加载"}).click();await expect(page.getByRole("heading",{name:"本次重训与原模型全部一致"})).toBeVisible();expect(new Set(paths).size).toBe(1);
});
test("training catalog hides wrong mode and recovers to an empty model scope",async({page})=>{
  await login(page);let wrong=true;
  await page.route("**/api/yta/v1/experimental-models/*/training-evidence?*",route=>route.fulfill({json:wrong?{...catalog,items:catalog.items.map((r:object)=>({...r,data_mode:"LIVE_RESEARCH"}))}:{items:[],next_cursor:null}}));
  await page.goto(modelPath);await expect(page.getByRole("main").getByRole("alert")).toContainText("目录暂时无法读取");await expect(page.getByRole("article")).toHaveCount(0);
  wrong=false;await page.getByRole("button",{name:"重新加载"}).click();await expect(page.getByRole("heading",{name:"暂无训练证据"})).toBeVisible();
});
