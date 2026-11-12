import { database } from "../../../../../packages/server/src/db";
import { createHandler } from "../../../../../packages/server/src/http";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const state = globalThis as unknown as {
  alphaPool?: ReturnType<typeof database>;
};
function handler(request: Request) {
  state.alphaPool ??= database();
  const origin = process.env.APP_ORIGIN;
  if (!origin)
    return Response.json(
      { error: "APP_ORIGIN_not_configured" },
      { status: 503 },
    );
  return createHandler(state.alphaPool, origin)(request);
}
export const GET = handler;
export const POST = handler;
export const PUT = handler;
