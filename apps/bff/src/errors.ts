import { ArgumentsHost, Catch, ExceptionFilter, HttpException } from "@nestjs/common";
import type { Response } from "express";
import { ZodError } from "zod";

export class ApiError extends Error {
  constructor(public readonly code: string, message: string, public readonly status = 400, public readonly retryable = false) {
    super(message);
  }
}

@Catch()
export class ErrorFilter implements ExceptionFilter {
  catch(error: unknown, host: ArgumentsHost) {
    const response = host.switchToHttp().getResponse<Response>();
    if (response.headersSent) { response.end(); return; }
    const known = error instanceof ApiError ? error : error instanceof ZodError
      ? new ApiError("INVALID_REQUEST", `Invalid fields: ${error.issues.map(i => i.path.join(".")).join(", ")}`, 422)
      : error instanceof HttpException
      ? new ApiError(`HTTP_${error.getStatus()}`, "Request could not be processed", error.getStatus())
      : new ApiError("INTERNAL_ERROR", "Request failed", 500, true);
    if (!(error instanceof ApiError || error instanceof ZodError || error instanceof HttpException)) {
      console.error(JSON.stringify({ event: "bff.error", request_id: response.locals.requestId, kind: error instanceof Error ? error.name : "unknown" }));
    }
    response.status(known.status).json({ error: { code: known.code, message: known.message, request_id: response.locals.requestId, retryable: known.retryable } });
  }
}
