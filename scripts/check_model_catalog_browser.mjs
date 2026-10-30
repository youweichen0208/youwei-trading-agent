// Build current Alpha training/BFF/Web images first. Requires local yta_test with
// at least eleven registered synthetic REPLAY models and both report fixtures registered
// in yta_test (migrations through 023), plus registered REPLAY five-session historical and probability quality policies.
// Both fixed backtest fixtures must also be registered in yta_test.
// Only a newly created synthetic user receives reviewer permission in the temporary BFF.
// No mocked responses; immutable test proposals/reviews and audits remain after cleanup.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, writeFile, readFile, rm, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { chromium } from "@playwright/test";

const run = promisify(execFile);
const reviewerId = randomUUID();
const project = "yta-model-browser-" + randomUUID().slice(0, 8);
const directory = await mkdtemp(join(tmpdir(), "yta-model-browser-"));
const override = join(directory, "probe.json");
const artifacts = ".local/model-catalog-browser";
await mkdir(artifacts, {recursive: true, mode: 0o700});
await writeFile(override, JSON.stringify({services: {"yta-bff": {environment: {YTA_MODEL_REVIEWER_IDS: reviewerId}, command: [
  "node", "--input-type=module", "-e",
  "import {createBff} from './dist/app.js';const s=await createBff({startRunner:false});" +
  "await s.app.listen(3100,'0.0.0.0');process.once('SIGTERM',()=>s.close().then(()=>process.exit(0)));",
]}}}), {mode: 0o600});
const compose = ["compose", "-p", project, "-f", "infra/compose/compose.app-local.yaml",
  "-f", "infra/compose/compose.experimental-inference.yaml", "-f", override];
async function docker(args, input) {
  const child = run("docker", args, {timeout: 180000, maxBuffer: 4_000_000});
  if (input) child.child.stdin.end(input);
  return (await child).stdout.trim();
}
const reportFixture = JSON.parse(await readFile("packages/contracts/fixtures/experimental-price-report.json", "utf8"));
const comparisonFixture = JSON.parse(await readFile("packages/contracts/fixtures/experimental-report-comparison.json", "utf8"));
const reportIdentity = "model-evaluation-experimental-" + reportFixture.content_hash;
const base = "http://localhost:3300";
let browser, page, stage = "start", loggedIn = false;
const evidence = {scope: "Real browser -> Web -> BFF -> Alpha -> PostgreSQL; synthetic REPLAY",
  response_mocking: false, research_runner: false, images: {}};
try {
  await docker([...compose, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120",
    "alpha-engine", "yta-bff", "yta-web"]);
  stage = "isolation";
  for (const service of ["alpha-engine", "yta-bff", "yta-web"]) {
    const id = await docker([...compose, "ps", "-q", service]);
    const state = JSON.parse(await docker(["inspect", id]))[0];
    assert(state.HostConfig.ReadonlyRootfs);
    assert(["node", "10001:10001"].includes(state.Config.User));
    assert(state.HostConfig.CapDrop.includes("ALL"));
    const ports = state.HostConfig.PortBindings;
    if (service === "yta-web") assert.deepEqual(ports, {"3000/tcp": [{HostIp: "127.0.0.1", HostPort: "3300"}]});
    else assert(!ports || Object.keys(ports).length === 0);
    evidence.images[service] = {id: state.Image, user: state.Config.User, read_only: true};
  }
  await docker([...compose, "exec", "-T", "yta-web", "node", "--input-type=module", "-e", `
    import assert from 'node:assert/strict';import dns from 'node:dns/promises';
    assert(!process.env.YTA_ALPHA_TOKEN && !process.env.YTA_DATABASE_URL);
    for(const host of ['alpha-engine','postgres']) {
      await assert.rejects(dns.lookup(host),{code:'ENOTFOUND'});
    }
  `]);
  stage = "fixture";
  // Credential output is consumed in memory, never logged or saved.
  const fixture = JSON.parse(await docker([...compose, "exec", "-T", "yta-bff", "node", "--input-type=module"], `
    import assert from 'node:assert/strict';import {randomUUID,randomBytes} from 'node:crypto';
    import {database} from './dist/database.js';import {hashPassword} from './dist/auth.js';
    assert(process.env.YTA_ENV==='test' && process.env.YTA_DATA_MODE==='REPLAY');
    assert(new URL(process.env.YTA_DATABASE_URL).pathname.endsWith('_test'));
    const pool=database(process.env.YTA_DATABASE_URL);
    try {
      const models=await pool.query("SELECT id FROM yta.model_versions WHERE model_kind='TRAINED' AND config->'artifact'->>'data_mode'='REPLAY' ORDER BY created_at DESC,id DESC LIMIT 20");
      assert(models.rows.length>10,'Requires at least eleven REPLAY models');
      const reports=await pool.query("SELECT id,payload FROM yta.model_evaluation_records WHERE id=$1 AND data_mode='REPLAY'",[${JSON.stringify(reportIdentity)}]);
      assert.equal(reports.rows.length,1,'Requires the registered REPLAY report fixture');
      const compared=await pool.query("SELECT id,payload FROM yta.model_evaluation_records WHERE id=ANY($1::text[]) AND data_mode='REPLAY'",[${JSON.stringify(comparisonFixture.evaluation_ids)}]);
      assert.equal(compared.rows.length,2,'Requires two registered comparison reports');
      const id=${JSON.stringify(reviewerId)},email=id+'@model-browser.test',password=randomBytes(24).toString('hex');
      await pool.query('INSERT INTO yta.users(id,email) VALUES($1,$2)',[id,email]);
      await pool.query('INSERT INTO yta.login_credentials(owner_id,password_hash) VALUES($1,$2)',[id,await hashPassword(password)]);
      console.log(JSON.stringify({email,password,ids:models.rows.map(row=>row.id),report:reports.rows[0].payload,comparison:compared.rows}));
    } finally {await pool.end();}
  `));
  browser = await chromium.launch();
  page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [], requests = [];
  page.on("pageerror", error => errors.push(error.name));
  page.on("request", request => requests.push({origin: new URL(request.url()).origin,
    path: new URL(request.url()).pathname, method: request.method()}));
  stage = "login";
  await page.goto(base + "/models");
  await page.waitForURL("**/login");
  await page.getByLabel("邮箱").fill(fixture.email);
  await page.getByLabel("密码").fill(fixture.password);
  await page.getByRole("button", {name: "进入研究工作台"}).click();
  await page.waitForURL(url => !url.pathname.endsWith("/login"));
  loggedIn = true;
  const me = await (await page.request.get(base + "/api/yta/v1/me")).json();
  assert.equal(me.experimental_models_enabled, true);
  assert.equal(me.can_review_models, true);
  stage = "first_page";
  const firstResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/experimental-models");
  await page.getByRole("navigation").getByRole("link", {name: "实验模型"}).click();
  const first = await (await firstResponse).json();
  assert.deepEqual(first.items.map(row => row.id), fixture.ids.slice(0, 10));
  await page.getByRole("article").nth(9).waitFor();
  assert.equal(await page.getByRole("article").count(), 10);
  await page.getByRole("article").first().getByText("核对完整版本与时间").click();
  await page.getByText(first.items[0].id, {exact: true}).waitFor();
  assert((await page.getByRole("article").first().innerText()).includes(first.items[0].reserved_window_mae));
  await page.screenshot({path: artifacts + "/desktop.png", fullPage: true});
  stage = "second_page";
  const secondResponse = page.waitForResponse(response => new URL(response.url()).searchParams.get("cursor") === first.next_cursor);
  await page.getByRole("button", {name: "下一页"}).click();
  const second = await (await secondResponse).json();
  assert.deepEqual(second.items.map(row => row.id), fixture.ids.slice(10, 20));
  await page.getByRole("article").first().waitFor();
  stage = "filter_mobile";
  const filteredResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/experimental-models"
    && new URL(response.url()).searchParams.get("horizon_sessions") === "5");
  await page.getByLabel("预测跨度").selectOption("5");
  const filtered = await (await filteredResponse).json();
  assert(filtered.items.length > 0 && filtered.items.every(row => row.horizon_sessions === 5 && row.data_mode === "REPLAY"));
  await page.getByRole("article").first().waitFor();
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole("article").first().getByText("核对完整版本与时间").click();
  await page.getByText(filtered.items[0].id, {exact: true}).waitFor();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path: artifacts + "/mobile.png", fullPage: true});
  assert.deepEqual(errors, []);
  assert(requests.every(request => request.origin === base && !request.path.startsWith("/internal/")));
  evidence.browser = {authenticated: true, database_order_equal: true, first_page: first.items.length,
    second_page: second.items.length, filtered_horizon: 5, filtered_count: filtered.items.length,
    full_identity_visible: true, decimal_string_visible: true, mobile_width: 390,
    document_overflow: false, page_errors: 0, same_origin_only: true};
  stage = "report_list";
  assert.deepEqual(fixture.report, reportFixture);
  await page.setViewportSize({width: 1440, height: 1000});
  const reportListPath = "/api/yta/v1/experimental-models/" + reportFixture.model_id + "/evaluations";
  const reportListResponse = page.waitForResponse(response => new URL(response.url()).pathname === reportListPath);
  await page.goto(base + "/models/" + reportFixture.model_id + "/evaluations");
  const reportList = await (await reportListResponse).json();
  assert(reportList.items.some(row => row.id === reportIdentity));
  await page.getByRole("region", {name: "实验评测列表"}).waitFor();
  stage = "report_detail";
  const reportResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/experimental-evaluations/" + reportIdentity);
  await page.locator('a[href="/model-evaluations/' + reportIdentity + '"]').click();
  const reportHttp = await reportResponse;
  assert.equal(reportHttp.status(), 200);
  assert.deepEqual(await reportHttp.json(), fixture.report);
  const metrics = page.getByRole("region", {name: "同样本指标比较"});
  await metrics.waitFor();
  assert.equal(await metrics.locator("tbody tr").count(), 3);
  assert((await metrics.innerText()).includes(reportFixture.metrics.model.mae));
  assert.equal(await page.getByRole("region", {name: "逐样本评测结果"}).locator("tbody tr").count(), reportFixture.manifest.length);
  await page.getByText("核对逐样本证据与计算源码").click();
  assert((await page.locator(".report-manifest").innerText()).includes(reportFixture.manifest[0].observation_snapshot_hash));
  await page.screenshot({path: artifacts + "/report-desktop.png", fullPage: true});
  stage = "report_mobile";
  await page.setViewportSize({width: 390, height: 844});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path: artifacts + "/report-mobile.png", fullPage: true});
  assert.deepEqual(errors, []);
  assert(requests.every(request => request.origin === base && !request.path.startsWith("/internal/")));
  evidence.report = {id: reportIdentity, database_payload_equal: true, list_to_detail: true,
    sample_count: reportFixture.manifest.length, decimal_string_visible: true,
    snapshot_hash_visible: true, mobile_width: 390, document_overflow: false, page_errors: 0};
  stage = "comparison_selection";
  const savedReports = comparisonFixture.evaluation_ids.map(id => fixture.comparison.find(row => row.id === id).payload);
  assert.deepEqual(savedReports, comparisonFixture.reports);
  assert.equal(new Set(savedReports.map(report => report.model_id)).size, 2);
  await page.setViewportSize({width: 1440, height: 1000});
  for (const [index, report] of savedReports.entries()) {
    await page.goto(base + "/models/" + report.model_id + "/evaluations");
    const link = page.locator('a[href="/model-evaluations/' + comparisonFixture.evaluation_ids[index] + '"]');
    await link.waitFor();
    await link.locator("..").getByRole("button", {name: "加入比较", exact: true}).click();
    await link.locator("..").getByRole("button", {name: "移出比较", exact: true}).waitFor();
  }
  await page.getByRole("link", {name: "比较已选报告（2） →"}).click();
  await page.waitForURL("**/models/compare");
  await page.getByRole("heading", {name: "实验报告比较", exact: true}).waitFor();
  await page.reload();
  await page.getByRole("heading", {name: "已选报告 · 2 / 10"}).waitFor();
  stage = "comparison_result";
  const comparedResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/experimental-evaluations/compare");
  await page.getByRole("button", {name: "比较所选报告", exact: true}).click();
  const comparedHttp = await comparedResponse;
  assert.equal(comparedHttp.status(), 200);
  assert.deepEqual(comparedHttp.request().postDataJSON(), {evaluation_ids: comparisonFixture.evaluation_ids});
  assert.deepEqual(await comparedHttp.json(), comparisonFixture);
  const comparedRegion = page.getByRole("region", {name: "多报告指标比较"});
  await comparedRegion.waitFor();
  assert.equal(await comparedRegion.locator("tbody tr").count(), 6);
  assert((await comparedRegion.innerText()).includes(savedReports[0].metrics.model.mae));
  await page.getByText("核对共同样本与完整模型身份").click();
  await page.getByText("样本集合：" + comparisonFixture.cohort_hash, {exact: true}).waitFor();
  await page.screenshot({path: artifacts + "/comparison-desktop.png", fullPage: true});
  stage = "comparison_mobile";
  await page.setViewportSize({width: 390, height: 844});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path: artifacts + "/comparison-mobile.png", fullPage: true});
  await page.getByRole("button", {name: "移除报告 1", exact: true}).click();
  await comparedRegion.waitFor({state: "detached"});
  assert(await page.getByRole("button", {name: "比较所选报告", exact: true}).isDisabled());
  assert.deepEqual(errors, []);
  assert(requests.every(request => request.origin === base && !request.path.startsWith("/internal/")));
  evidence.comparison = {database_payloads_equal: true, explicit_request_order: true,
    different_models: 2, same_cohort: comparisonFixture.cohort_hash, response_mocking: false,
    refresh_preserves_selection: true, removal_clears_result: true,
    mobile_width: 390, document_overflow: false, page_errors: 0};
  stage = "proposal_form";
  const proposalPath = `/models/propose/${comparisonFixture.evaluation_ids[0]}/${comparisonFixture.evaluation_ids[1]}`;
  await page.setViewportSize({width: 1440, height: 1000});
  await page.goto(base + proposalPath);
  await page.getByLabel("提出理由").waitFor();
  await page.getByLabel("提出理由").fill("容器端到端候选提案复核 🧪；固定历史证据，未批准晋升。");
  await page.screenshot({path: artifacts + "/proposal-form-desktop.png", fullPage: true});
  stage = "proposal_submit";
  const submittedResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/model-proposals" && response.request().method() === "POST");
  await page.getByRole("button", {name: "提交候选提案", exact: true}).click();
  const submittedHttp = await submittedResponse;
  assert.equal(submittedHttp.status(), 200);
  const submitted = await submittedHttp.json();
  const submittedBody = submittedHttp.request().postDataJSON();
  assert.equal(submitted.proposal.owner_id, me.user.id);
  assert.equal(submitted.proposal.request_id, submittedHttp.request().headers()["idempotency-key"]);
  assert.equal(submitted.proposal.status, "SUBMITTED");
  assert.equal(submitted.proposal.qualification, "UNVERIFIED");
  assert.equal(submitted.proposal.candidate_evaluation_id, comparisonFixture.evaluation_ids[0]);
  assert.equal(submitted.proposal.reference_evaluation_id, comparisonFixture.evaluation_ids[1]);
  await page.getByRole("heading", {name: "提案已提交", exact: true}).waitFor();
  stage = "proposal_retry";
  const exactRetry = await page.request.post(base + "/api/yta/v1/model-proposals", {
    headers: {Origin: base, "X-CSRF-Token": me.csrf_token, "Idempotency-Key": submitted.proposal.request_id},
    data: submittedBody,
  });
  assert.equal(exactRetry.status(), 200);
  assert.deepEqual(await exactRetry.json(), submitted);
  stage = "proposal_detail";
  const detailResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/model-proposals/" + submitted.id);
  await page.getByRole("link", {name: "打开提案详情 →"}).click();
  await page.waitForURL("**/model-proposals/" + submitted.id);
  assert.deepEqual(await (await detailResponse).json(), submitted);
  await page.getByRole("heading", {name: "提案已提交", exact: true}).waitFor();
  await page.screenshot({path: artifacts + "/proposal-desktop.png", fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path: artifacts + "/proposal-mobile.png", fullPage: true});
  stage = "proposal_database";
  const proposalDatabase = JSON.parse(await docker([...compose, "exec", "-T", "yta-bff", "node", "--input-type=module"], `
    import {database} from './dist/database.js';
    const pool=database(process.env.YTA_DATABASE_URL);
    try {
      const rows=await pool.query('SELECT payload,content_hash FROM yta.model_proposals WHERE owner_id=$1 AND request_id=$2',
        [${JSON.stringify(me.user.id)},${JSON.stringify(submitted.proposal.request_id)}]);
      const audit=await pool.query("SELECT count(*)::int AS n FROM yta.audit_events WHERE action='model.proposal_submitted' AND actor_id=$1 AND resource_id=$2",
        [${JSON.stringify(me.user.id)},${JSON.stringify(submitted.id)}]);
      console.log(JSON.stringify({rows:rows.rows,audits:audit.rows[0].n}));
    } finally {await pool.end();}
  `));
  assert.equal(proposalDatabase.rows.length, 1);
  assert.equal(proposalDatabase.audits, 1);
  assert.deepEqual(proposalDatabase.rows[0], {payload: submitted.proposal, content_hash: submitted.content_hash});
  stage = "proposal_return";
  await page.goto(base + proposalPath);
  await page.getByRole("link", {name: "查看已提交提案 →"}).waitFor();
  assert.equal(await page.getByRole("button", {name: "提交候选提案", exact: true}).count(), 0);
  stage = "proposal_history_seed";
  const historyRecords = [submitted];
  for (let i = 0; i < 10; i++) {
    const created = await page.request.post(base + "/api/yta/v1/model-proposals", {
      headers: {Origin: base, "X-CSRF-Token": me.csrf_token, "Idempotency-Key": randomUUID()},
      data: {...submittedBody, rationale: `容器历史分页复核 ${i + 1}；合成 REPLAY，未批准。`},
    });
    assert.equal(created.status(), 200);
    historyRecords.push(await created.json());
  }
  stage = "proposal_history";
  await page.evaluate(() => sessionStorage.clear());
  const historyResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/model-proposals" && response.request().method() === "GET");
  await page.getByRole("link", {name: "查看我的提案历史 →"}).click();
  const historyHttp = await historyResponse;
  assert.equal(historyHttp.status(), 200);
  const historyPage = await historyHttp.json();
  const expectedHistory = historyRecords.slice().reverse();
  assert.deepEqual(historyPage, {items: expectedHistory.slice(0, 10), next_cursor: expectedHistory[9].id});
  await page.getByRole("article").nth(9).waitFor();
  assert.equal(await page.getByRole("article").count(), 10);
  await page.screenshot({path: artifacts + "/proposal-history-desktop.png", fullPage: true});
  const nextHistoryResponse = page.waitForResponse(response => new URL(response.url()).searchParams.get("cursor") === historyPage.next_cursor);
  await page.getByRole("button", {name: "下一页", exact: true}).click();
  assert.deepEqual(await (await nextHistoryResponse).json(), {items: [submitted], next_cursor: null});
  await page.getByRole("link", {name: `查看提案 ${submitted.id.slice(0, 8)} →`}).waitFor();
  assert.equal(await page.getByRole("article").count(), 1);
  assert(await page.getByRole("button", {name: "下一页", exact: true}).isDisabled());
  await page.setViewportSize({width: 390, height: 844});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path: artifacts + "/proposal-history-mobile.png", fullPage: true});
  await page.getByRole("link", {name: `查看提案 ${submitted.id.slice(0, 8)} →`}).click();
  await page.getByRole("heading", {name: "模型提案详情", exact: true}).waitFor();
  await page.getByText(submitted.proposal.rationale, {exact: true}).waitFor();
  evidence.proposal_history = {records: 11, page_sizes: [10, 1], responses_equal_registered_records: true,
    session_storage_cleared: true, detail_navigation: true, mobile_width: 390, document_overflow: false};
  stage = "quality_rule_selection";
  await page.setViewportSize({width: 1440, height: 1000});
  const rulesResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/historical-quality-policies");
  await page.getByRole("link", {name: "核验本提案的历史质量 →"}).click();
  const rulesHttp = await rulesResponse;
  assert.equal(rulesHttp.status(), 200);
  const rules = await rulesHttp.json();
  assert(rules.items.length > 0);
  assert(rules.items.every(row => row.policy.data_mode === "REPLAY" && row.policy.horizon_sessions === 5));
  const selectedRule = rules.items[0];
  await page.getByRole("radio").first().waitFor();
  assert(await page.getByRole("button", {name: "按所选规则核验", exact: true}).isDisabled());
  await page.getByRole("radio").first().check();
  stage = "quality_submit";
  const qualityResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/historical-quality-assessments" && response.request().method() === "POST");
  await page.getByRole("button", {name: "按所选规则核验", exact: true}).click();
  const qualityHttp = await qualityResponse;
  assert.equal(qualityHttp.status(), 200);
  const quality = await qualityHttp.json(), qualityBody = qualityHttp.request().postDataJSON();
  assert.deepEqual(qualityBody, {policy_id: selectedRule.id,
    candidate_evaluation_id: submitted.proposal.candidate_evaluation_id,
    reference_evaluation_id: submitted.proposal.reference_evaluation_id});
  assert.equal(quality.policy_hash, selectedRule.content_hash);
  assert.deepEqual(quality.policy, selectedRule.policy);
  assert.equal(quality.promotion_allowed, false);
  assert.equal(quality.qualification, "UNVERIFIED");
  await page.getByRole("region", {name: "质量核验明细"}).waitFor();
  await page.screenshot({path: artifacts + "/quality-desktop.png", fullPage: true});
  stage = "quality_retry";
  const qualityRetry = await page.request.post(base + "/api/yta/v1/historical-quality-assessments", {
    headers: {Origin: base, "X-CSRF-Token": me.csrf_token}, data: qualityBody,
  });
  assert.equal(qualityRetry.status(), 200);
  assert.deepEqual(await qualityRetry.json(), quality);
  stage = "quality_detail";
  const qualityId = "historical-quality-assessment-" + quality.content_hash;
  const qualityDetail = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/historical-quality-assessments/" + qualityId);
  await page.getByRole("link", {name: "打开固定核验详情 →"}).click();
  assert.deepEqual(await (await qualityDetail).json(), quality);
  await page.getByRole("heading", {name: "质量核验详情", exact: true}).waitFor();
  await page.reload();
  await page.getByRole("region", {name: "质量核验明细"}).waitFor();
  await page.setViewportSize({width: 390, height: 844});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path: artifacts + "/quality-mobile.png", fullPage: true});
  stage = "quality_database";
  const qualityDatabase = JSON.parse(await docker([...compose, "exec", "-T", "yta-bff", "node", "--input-type=module"], `
    import {Pool} from 'pg';
    const pool=new Pool({connectionString:process.env.YTA_DATABASE_URL});
    try {
      const policies=await pool.query('SELECT payload FROM yta.model_quality_policies WHERE id=$1',[${JSON.stringify(selectedRule.id)}]);
      const assessments=await pool.query('SELECT payload FROM yta.model_quality_assessments WHERE id=$1',[${JSON.stringify(qualityId)}]);
      const audit=await pool.query("SELECT count(*)::int AS n FROM yta.audit_events WHERE resource_id=$1 AND action='model.quality_assessment_registered'",[${JSON.stringify(qualityId)}]);
      console.log(JSON.stringify({policies:policies.rows,assessments:assessments.rows,audits:audit.rows[0].n}));
    } finally {await pool.end();}
  `));
  assert.deepEqual(qualityDatabase.policies, [{payload: selectedRule.policy}]);
  assert.deepEqual(qualityDatabase.assessments, [{payload: quality}]);
  assert.equal(qualityDatabase.audits, 1);
  evidence.quality = {policy_id: selectedRule.id, assessment_id: qualityId, explicit_selection: true,
    policy_database_equal: true, assessment_database_equal: true, exact_retry_equal: true,
    fixed_detail_equal: true, detail_reload: true, facts: 1, audits: 1,
    promotion_allowed: false, qualification: "UNVERIFIED", mobile_width: 390, document_overflow: false};
  assert.deepEqual(errors, []);
  assert(requests.every(request => request.origin === base && !request.path.startsWith("/internal/")));
  evidence.proposal = {id: submitted.id, authenticated_owner: true, response_mocking: false,
    explicit_submit: true, exact_retry_equal: true, detail_equal: true, database_payload_equal: true,
    facts: 1, audits: 1, returning_form_links_existing: true, mobile_width: 390,
    document_overflow: false, page_errors: 0, status: "SUBMITTED", qualification: "UNVERIFIED"};
  stage = "review_inbox";
  await page.setViewportSize({width: 1440, height: 1000});
  const inboxResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/model-review-proposals");
  await page.getByRole("navigation").getByRole("link", {name: "模型审核", exact: true}).click();
  const inboxHttp = await inboxResponse;
  assert.equal(inboxHttp.status(), 200);
  const inbox = await inboxHttp.json();
  assert(inbox.items.length > 0);
  await page.getByRole("heading", {name: "审核模型提案", exact: true}).waitFor();
  stage = "review_form";
  await page.goto(base + "/model-review-proposals/" + submitted.id);
  await page.getByRole("heading", {name: "固定提案", exact: true}).waitFor();
  assert(await page.getByRole("button", {name: "确认提交审核", exact: true}).isDisabled());
  let reviewGate, reviewProbability;
  async function assessForReview() {
    const priceSection=page.getByLabel("价格证据",{exact:true}),probabilitySection=page.getByLabel("概率证据",{exact:true});
    await priceSection.getByRole("radio").first().check();
    const response = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/historical-quality-assessments" && response.request().method() === "POST");
    await priceSection.getByRole("button", {name: "按所选规则核验", exact: true}).click();
    const http = await response;
    assert.equal(http.status(), 200);
    assert.deepEqual(await http.json(), quality);
    await page.getByRole("region", {name: "质量核验明细"}).waitFor();
    await probabilitySection.getByRole("radio").first().check();
    const probabilityResponse=page.waitForResponse(response=>new URL(response.url()).pathname==="/api/yta/v1/probability-quality-assessments" && response.request().method()==="POST");
    await probabilitySection.getByRole("button",{name:"按所选规则核验",exact:true}).click();
    const probabilityHttp=await probabilityResponse;assert.equal(probabilityHttp.status(),200);
    reviewProbability=await probabilityHttp.json();
    const gateResponse=page.waitForResponse(response=>new URL(response.url()).pathname==="/api/yta/v1/model-review-quality-gates" && response.request().method()==="POST");
    await page.getByRole("button",{name:"固定组合证据",exact:true}).click();
    const gateHttp=await gateResponse;assert.equal(gateHttp.status(),200);
    const gate=await gateHttp.json();
    assert.equal(gate.proposal_id,submitted.id);assert.equal(gate.proposal_content_hash,submitted.content_hash);
    assert.equal(gate.price_assessment_id,qualityId);assert.equal(gate.probability_assessment_id,"probability-quality-assessment-"+reviewProbability.content_hash);
    if(reviewGate)assert.deepEqual(gate,reviewGate);
    reviewGate=gate;
    await page.getByRole("heading",{name:"组合证据已固定 · 尚未批准",exact:true}).waitFor();
    const retried=await page.request.post(base+"/api/yta/v1/model-review-quality-gates",{headers:{Origin:base,"X-CSRF-Token":me.csrf_token},data:gateHttp.request().postDataJSON()});
    assert.equal(retried.status(),200);assert.deepEqual(await retried.json(),gate);
  }
  await assessForReview();
  await page.getByLabel("审核动作").selectOption("APPROVE");
  await page.getByLabel("审核理由").fill("合成回放容器验证：固定证据尚未取得晋升资格。");
  await page.screenshot({path: artifacts + "/review-form-desktop.png", fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path: artifacts + "/review-form-mobile.png", fullPage: true});
  stage = "review_submit";
  const reviewResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/model-reviews" && response.request().method() === "POST");
  await page.getByRole("button", {name: "确认提交审核", exact: true}).click();
  const reviewHttp = await reviewResponse;
  assert.equal(reviewHttp.status(), 200);
  const review = await reviewHttp.json(), reviewBody = reviewHttp.request().postDataJSON();
  const reviewKey = reviewHttp.request().headers()["idempotency-key"];
  assert.equal(review.review.reviewer_id, reviewerId);
  assert.equal(review.review.request_id, reviewKey);
  assert.equal(review.review.proposal_id, submitted.id);
  assert.equal(review.review.schema_version,"model-review-record-v2");
  assert.equal(review.review.price_assessment_id, qualityId);
  assert.equal(review.review.quality_gate_id,"model-quality-gate-"+reviewGate.content_hash);
  assert.equal(reviewBody.quality_gate_id,review.review.quality_gate_id);
  assert.equal(review.review.disposition, "BLOCKED");
  assert.equal(review.review.promotion_allowed, false);
  assert.equal(reviewBody.expected_previous_review_id, null);
  stage = "review_retry";
  const retryReview = await page.request.post(base + "/api/yta/v1/model-reviews", {
    headers: {Origin: base, "X-CSRF-Token": me.csrf_token, "Idempotency-Key": reviewKey}, data: reviewBody,
  });
  assert.equal(retryReview.status(), 200);
  assert.deepEqual(await retryReview.json(), review);
  await page.getByRole("link", {name: "打开审核记录 →", exact: true}).click();
  await page.waitForURL("**/model-reviews/" + review.id);
  await page.reload();
  await page.getByRole("heading", {name: "模型审核记录", exact: true}).waitFor();
  await page.getByText("晋升受阻 · 未批准", {exact: true}).waitFor();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path: artifacts + "/review-mobile.png", fullPage: true});
  stage = "review_reject";
  await page.setViewportSize({width: 1440, height: 1000});
  await page.getByRole("link", {name: "返回提案审核 →", exact: true}).click();
  await page.getByRole("button", {name: "重新加载最新状态并核对", exact: true}).click();
  await page.getByRole("heading", {name: "固定提案", exact: true}).waitFor();
  await assessForReview();
  await page.getByLabel("审核动作").selectOption("REJECT");
  await page.getByLabel("审核理由").fill("合成回放容器验证：拒绝当前提案并关闭审核链。");
  const rejectResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/model-reviews" && response.request().method() === "POST");
  await page.getByRole("button", {name: "确认提交审核", exact: true}).click();
  const rejectedHttp = await rejectResponse;
  assert.equal(rejectedHttp.status(), 200);
  const rejected = await rejectedHttp.json();
  assert.equal(rejected.review.previous_review_id, review.id);
  assert.equal(rejected.review.revision, 2);
  assert.equal(rejected.review.schema_version,"model-review-record-v2");
  assert.equal(rejected.review.quality_gate_id,review.review.quality_gate_id);
  assert.equal(rejected.review.disposition, "REJECTED");
  assert.equal(rejected.review.promotion_allowed, false);
  await page.getByRole("button", {name: "重新加载最新状态并核对", exact: true}).click();
  await page.getByRole("heading", {name: "提案已拒绝，不能继续审核", exact: true}).waitFor();
  assert.equal(await page.getByRole("button", {name: "确认提交审核", exact: true}).count(), 0);
  await page.screenshot({path: artifacts + "/review-history-desktop.png", fullPage: true});
  const history = await page.request.get(base + "/api/yta/v1/model-proposals/" + submitted.id + "/reviews");
  assert.equal(history.status(), 200);
  assert.deepEqual(await history.json(), {items: [rejected, review], next_cursor: null});
  // Original exact retry still returns its immutable result after the proposal closes.
  const afterClosed = await page.request.post(base + "/api/yta/v1/model-reviews", {
    headers: {Origin: base, "X-CSRF-Token": me.csrf_token, "Idempotency-Key": reviewKey}, data: reviewBody,
  });
  assert.equal(afterClosed.status(), 200);
  assert.deepEqual(await afterClosed.json(), review);
  stage = "review_database";
  const reviewDatabase = JSON.parse(await docker([...compose, "exec", "-T", "yta-bff", "node", "--input-type=module"], `
    import {Pool} from 'pg';
    const pool=new Pool({connectionString:process.env.YTA_DATABASE_URL});
    try {
      const rows=await pool.query('SELECT payload FROM yta.model_reviews WHERE proposal_id=$1 ORDER BY revision',[${JSON.stringify(submitted.id)}]);
      const audit=await pool.query("SELECT actor_id,resource_id FROM yta.audit_events WHERE action='model.review_recorded' AND resource_id=ANY($1::text[]) ORDER BY occurred_at,id",[${JSON.stringify([review.id, rejected.id])}]);
      const gates=await pool.query('SELECT payload FROM yta.model_quality_gates WHERE id=$1',[${JSON.stringify("model-quality-gate-"+reviewGate.content_hash)}]);
      const gateAudits=await pool.query("SELECT actor_id FROM yta.audit_events WHERE action='model.quality_gate_registered' AND resource_id=$1",[${JSON.stringify("model-quality-gate-"+reviewGate.content_hash)}]);
      console.log(JSON.stringify({rows:rows.rows,audits:audit.rows,gates:gates.rows,gateAudits:gateAudits.rows}));
    } finally {await pool.end();}
  `));
  assert.deepEqual(reviewDatabase.gates,[{payload:reviewGate}]);
  assert.deepEqual(reviewDatabase.gateAudits,[{actor_id:reviewerId}]);
  assert.deepEqual(reviewDatabase.rows, [{payload: review.review}, {payload: rejected.review}]);
  assert.deepEqual(reviewDatabase.audits.map(row => row.resource_id), [review.id, rejected.id]);
  assert(reviewDatabase.audits.every(row => row.actor_id === reviewerId));
  assert.deepEqual(errors, []);
  assert(requests.every(request => request.origin === base && !request.path.startsWith("/internal/")));
  evidence.review = {schema_version:"model-review-record-v2",combined_gate:"model-quality-gate-"+reviewGate.content_hash,combined_gate_facts:1,combined_gate_audits:1,probability_assessment:"probability-quality-assessment-"+reviewProbability.content_hash,explicit_probability_selection:true,synthetic_reviewer_only: true, inbox: true, explicit_quality_selection: true,
    explicit_submit: true, exact_retry_equal: true, retry_after_closed_equal: true,
    fixed_detail_reload: true, database_payload_equal: true, authenticated_audit_actor: true,
    records: 2, audits: 2, dispositions: ["BLOCKED", "REJECTED"], promotion_allowed: false,
    predecessor_binding: true, rejected_ui_closed: true, full_history_equal: true,
    mobile_width: 390, document_overflow: false, page_errors: 0, response_mocking: false};
  stage = "calibration_report";
  const expectedCalibration = JSON.parse(await readFile("packages/contracts/fixtures/calibration-report.json", "utf8"));
  assert.equal(expectedCalibration.id, reportFixture.calibration_id);
  await page.setViewportSize({width: 1440, height: 1000});
  await page.goto(base + "/model-evaluations/" + reportIdentity);
  const calibrationResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/experimental-calibrations/" + expectedCalibration.id);
  await page.getByRole("link", {name: "查看固定概率校准报告 →", exact: true}).click();
  const calibrationHttp = await calibrationResponse;
  assert.equal(calibrationHttp.status(), 200);
  assert.deepEqual(await calibrationHttp.json(), expectedCalibration);
  const bins = page.getByRole("region", {name: "概率可靠性分箱", exact: true});
  await bins.waitFor();
  assert.equal(await bins.locator("tbody tr").count(), 10);
  assert((await page.getByRole("main").innerText()).includes(expectedCalibration.report.reliability.brier));
  assert((await page.getByRole("main").innerText()).includes(expectedCalibration.report.reliability.ece));
  await page.getByText("核对拟合窗口、结点与代码身份", {exact: true}).click();
  assert((await page.locator(".report-manifest").innerText()).includes(expectedCalibration.report.calibration_ids[0]));
  await page.screenshot({path: artifacts + "/calibration-desktop.png", fullPage: true});
  await page.reload();
  await bins.waitFor();
  await page.setViewportSize({width: 390, height: 844});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path: artifacts + "/calibration-mobile.png", fullPage: true});
  const calibrationDatabase = JSON.parse(await docker([...compose, "exec", "-T", "yta-bff", "node", "--input-type=module"], `
    import {Pool} from 'pg';
    const pool=new Pool({connectionString:process.env.YTA_DATABASE_URL});
    try {
      const rows=await pool.query('SELECT payload FROM yta.calibration_records WHERE id=$1',[${JSON.stringify(expectedCalibration.id)}]);
      console.log(JSON.stringify(rows.rows[0].payload.report));
    } finally {await pool.end();}
  `));
  assert.deepEqual(calibrationDatabase, expectedCalibration.report);
  assert.deepEqual(errors, []);
  assert(requests.every(request => request.origin === base && !request.path.startsWith("/internal/")));
  evidence.calibration = {id: expectedCalibration.id, fixed_report_link: true,
    response_fixture_equal: true, database_report_equal: true, bins: 10, detail_reload: true,
    raw_brier_ece_visible: true, source_identity_visible: true, qualification: "UNVERIFIED",
    mobile_width: 390, document_overflow: false, page_errors: 0, response_mocking: false};
  stage = "probability_rule_selection";
  await page.setViewportSize({width: 1440, height: 1000});
  const probabilityRulesResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/probability-quality-policies");
  await page.getByRole("link", {name: "选择规则并核验概率质量 →", exact: true}).click();
  const probabilityRulesHttp = await probabilityRulesResponse;
  assert.equal(probabilityRulesHttp.status(), 200);
  const probabilityRules = await probabilityRulesHttp.json();
  assert(probabilityRules.items.length > 0);
  assert(probabilityRules.items.every(row => row.policy.data_mode === "REPLAY" && row.policy.horizon_sessions === 5));
  const probabilityRule = probabilityRules.items[0];
  await page.getByRole("radio").first().waitFor();
  assert(await page.getByRole("button", {name: "按所选规则核验", exact: true}).isDisabled());
  await page.getByRole("radio").first().check();
  stage = "probability_submit";
  const probabilityResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/probability-quality-assessments" && response.request().method() === "POST");
  await page.getByRole("button", {name: "按所选规则核验", exact: true}).click();
  const probabilityHttp = await probabilityResponse;
  assert.equal(probabilityHttp.status(), 200);
  const probability = await probabilityHttp.json(), probabilityBody = probabilityHttp.request().postDataJSON();
  assert.deepEqual(probabilityBody, {policy_id: probabilityRule.id, calibration_id: expectedCalibration.id});
  assert.deepEqual(probability.policy, probabilityRule.policy);
  assert.equal(probability.policy_hash, probabilityRule.content_hash);
  assert.equal(probability.calibration_content_hash, expectedCalibration.content_hash);
  assert.equal(probability.report_hash, expectedCalibration.report_hash);
  assert.equal(probability.model_id, expectedCalibration.model_id);
  assert.equal(probability.promotion_allowed, false);
  assert.equal(probability.qualification, "UNVERIFIED");
  const probabilityTable = page.getByRole("region", {name: "概率核验明细", exact: true});
  await probabilityTable.waitFor();
  assert.equal(await probabilityTable.locator("tbody tr").count(), 6);
  assert((await probabilityTable.innerText()).includes(expectedCalibration.report.reliability.brier));
  assert((await probabilityTable.innerText()).includes(expectedCalibration.report.reliability.ece));
  await page.screenshot({path: artifacts + "/probability-quality-desktop.png", fullPage: true});
  stage = "probability_retry";
  const probabilityRetry = await page.request.post(base + "/api/yta/v1/probability-quality-assessments", {
    headers: {Origin: base, "X-CSRF-Token": me.csrf_token}, data: probabilityBody,
  });
  assert.equal(probabilityRetry.status(), 200);
  assert.deepEqual(await probabilityRetry.json(), probability);
  stage = "probability_detail";
  const probabilityId = "probability-quality-assessment-" + probability.content_hash;
  const probabilityDetail = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/probability-quality-assessments/" + probabilityId);
  await page.getByRole("link", {name: "打开固定核验详情 →", exact: true}).click();
  assert.deepEqual(await (await probabilityDetail).json(), probability);
  await page.getByRole("heading", {name: "概率核验详情", exact: true}).waitFor();
  await page.reload();
  await probabilityTable.waitFor();
  await page.setViewportSize({width: 390, height: 844});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path: artifacts + "/probability-quality-mobile.png", fullPage: true});
  stage = "probability_database";
  const probabilityDatabase = JSON.parse(await docker([...compose, "exec", "-T", "yta-bff", "node", "--input-type=module"], `
    import {Pool} from 'pg';
    const pool=new Pool({connectionString:process.env.YTA_DATABASE_URL});
    try {
      const policies=await pool.query('SELECT payload FROM yta.probability_quality_policies WHERE id=$1',[${JSON.stringify(probabilityRule.id)}]);
      const assessments=await pool.query('SELECT payload FROM yta.probability_quality_assessments WHERE id=$1',[${JSON.stringify(probabilityId)}]);
      const audit=await pool.query("SELECT count(*)::int AS n FROM yta.audit_events WHERE resource_id=$1 AND action='model.probability_quality_assessment_registered'",[${JSON.stringify(probabilityId)}]);
      console.log(JSON.stringify({policies:policies.rows,assessments:assessments.rows,audits:audit.rows[0].n}));
    } finally {await pool.end();}
  `));
  assert.deepEqual(probabilityDatabase.policies, [{payload: probabilityRule.policy}]);
  assert.deepEqual(probabilityDatabase.assessments, [{payload: probability}]);
  assert.equal(probabilityDatabase.audits, 1);
  assert.deepEqual(errors, []);
  assert(requests.every(request => request.origin === base && !request.path.startsWith("/internal/")));
  evidence.probability_quality = {policy_id: probabilityRule.id, assessment_id: probabilityId,
    calibration_id: expectedCalibration.id, explicit_selection: true, exact_retry_equal: true,
    checks: 6, raw_brier_ece_visible: true, response_database_equal: true, audit_count: 1,
    detail_reload: true, qualification: "UNVERIFIED", promotion_allowed: false,
    mobile_width: 390, document_overflow: false, page_errors: 0, response_mocking: false};

  stage = "backtest_catalog";
  const completeBacktest = JSON.parse(await readFile("packages/contracts/fixtures/backtest-report.json", "utf8"));
  const incompleteBacktest = JSON.parse(await readFile("packages/contracts/fixtures/backtest-report-incomplete.json", "utf8"));
  const backtestModel = completeBacktest.report.signal_sources[0].model_id;
  await page.setViewportSize({width: 1440, height: 1000});
  const backtestListResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/backtest-reports");
  await page.goto(base + "/backtests?model_id=" + backtestModel);
  const backtestList = await (await backtestListResponse).json();
  assert(backtestList.items.length > 0);
  assert(backtestList.items.every(row => row.data_mode === "REPLAY" && row.model_ids.includes(backtestModel)
    && row.verification === "stored-integrity-checked-native-replay-on-detail"));
  assert(backtestList.items.some(row => row.id === completeBacktest.id));
  await page.getByRole("link", {name: "回测 " + completeBacktest.content_hash.slice(0, 12), exact: true}).waitFor();
  await page.screenshot({path: artifacts + "/backtest-catalog-desktop.png", fullPage: true});
  const filteredBacktestResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/backtest-reports"
    && new URL(response.url()).searchParams.get("status") === "INCOMPLETE");
  await page.getByLabel("报告完整性").selectOption("INCOMPLETE");
  const filteredBacktests = await (await filteredBacktestResponse).json();
  assert(filteredBacktests.items.length > 0 && filteredBacktests.items.every(row => row.status === "INCOMPLETE"));
  const backtestDetails = [];
  for (const expected of [completeBacktest, incompleteBacktest]) {
    stage = "backtest_" + expected.report.status;
    const detailResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/backtest-reports/" + expected.id);
    await page.goto(base + "/backtests/" + expected.id);
    const backtestResponse = await detailResponse;
    stage = "backtest_" + expected.report.status + "_http";
    assert.equal(backtestResponse.status(), 200);
    stage = "backtest_" + expected.report.status + "_payload";
    assert.deepEqual(await backtestResponse.json(), expected);
    stage = "backtest_" + expected.report.status + "_render";
    await page.getByRole("heading", {name: "逐笔执行与退出权益", exact: true}).waitFor();
    if (expected.report.status === "INCOMPLETE") {
      assert.deepEqual(await page.locator(".model-metrics dd").allTextContents(), ["无法计算", "无法计算", "无法计算"]);
    } else assert.deepEqual(await page.locator(".model-metrics dd").allTextContents(), [
      expected.report.strategy_net_return, expected.report.benchmark_net_return, expected.report.maximum_realized_drawdown,
    ]);
    assert((await page.getByRole("region", {name: "回测逐笔执行"}).innerText()).includes("重叠排除"));
    const row = expected.report.rows[0];
    await page.locator("details.backtest-source").first().locator("summary").click();
    await page.locator("details.backtest-source").first().getByText(row.signal_id, {exact: true}).waitFor();
    await page.screenshot({path: artifacts + "/backtest-" + expected.report.status.toLowerCase() + "-desktop.png", fullPage: true});
    stage = "backtest_" + expected.report.status + "_refresh";
    const refreshed = page.waitForResponse(response => new URL(response.url()).pathname === "/api/yta/v1/backtest-reports/" + expected.id);
    await page.reload();
    assert.deepEqual(await (await refreshed).json(), expected);
    await page.getByRole("heading", {name: "逐笔执行与退出权益", exact: true}).waitFor();
    await page.setViewportSize({width: 390, height: 844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({path: artifacts + "/backtest-" + expected.report.status.toLowerCase() + "-mobile.png", fullPage: true});
    backtestDetails.push({id: expected.id, status: expected.report.status, exact_fixture_equal: true,
      detail_reload: true, mobile_width: 390, document_overflow: false});
    await page.setViewportSize({width: 1440, height: 1000});
  }
  stage = "backtest_database";
  const backtestStored = JSON.parse(await docker([...compose, "exec", "-T", "yta-bff", "node", "--input-type=module"], `
    import {Pool} from 'pg';const pool=new Pool({connectionString:process.env.YTA_DATABASE_URL});
    try {
      const rows=await pool.query('SELECT id,payload FROM yta.backtest_reports WHERE id=ANY($1::text[])',[
        ${JSON.stringify([completeBacktest.id, incompleteBacktest.id])}]);
      console.log(JSON.stringify(rows.rows));
    } finally {await pool.end();}
  `));
  assert.equal(backtestStored.length, 2);
  for (const expected of [completeBacktest, incompleteBacktest]) assert.deepEqual(
    backtestStored.find(row => row.id === expected.id).payload.report, expected.report);
  assert.deepEqual(errors, []);
  assert(requests.every(request => request.origin === base && !request.path.startsWith("/internal/")));
  evidence.backtests = {catalog_model_filter: true, catalog_status_filter: true,
    catalog_scope: "stored-integrity-checked-native-replay-on-detail", details: backtestDetails,
    database_reports_equal: true, page_errors: 0, response_mocking: false, qualification: "UNVERIFIED"};
  stage = "logout";
  await page.getByRole("button", {name: "退出", exact: true}).click();
  await page.waitForURL("**/login");
  loggedIn = false;
  evidence.observed_at = new Date().toISOString();
} catch {
  // Playwright diagnostics can include the filled synthetic login form.
  throw new Error("Model browser verification failed at stage: " + stage);
} finally {
  if (loggedIn && page) try {
    const me = await (await page.request.get(base + "/api/yta/v1/me")).json();
    await page.request.post(base + "/api/yta/v1/auth/logout", {
      headers: {Origin: base, "X-CSRF-Token": me.csrf_token}, data: {},
    }).catch(() => {});
  } catch { /* Cleanup must continue if the application is unavailable. */ }
  await browser?.close();
  try {await docker([...compose, "down", "--timeout", "10"]);}
  finally {await rm(directory, {recursive: true, force: true});}
}
evidence.containers_cleaned = true;
await writeFile(artifacts + "/observation.json", JSON.stringify(evidence, null, 2) + "\n");
console.log(JSON.stringify(evidence));
