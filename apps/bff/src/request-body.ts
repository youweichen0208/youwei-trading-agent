import express, { type RequestHandler } from "express";
import { ApiError } from "./errors.js";

const parser = express.json({limit: "32kb"});

// Interpret status/type only at the trusted parser callback boundary, never from
// application/provider errors or user JSON. Do not expose parser messages or body.
export const parseJsonBody: RequestHandler = (request, response, next) => {
  parser(request, response, (error?: unknown) => {
    if (error instanceof Error && "status" in error) {
      const type = "type" in error ? error.type : undefined;
      if (error.status === 413) return next(new ApiError("REQUEST_TOO_LARGE", "Request body exceeds 32 KiB", 413));
      if (error.status === 415) {
        return next(type === "charset.unsupported"
          ? new ApiError("UNSUPPORTED_CHARSET", "Request character set is unsupported", 415)
          : new ApiError("UNSUPPORTED_ENCODING", "Request content encoding is unsupported", 415));
      }
      if (error.status === 400) {
        return next(type === "entity.parse.failed"
          ? new ApiError("MALFORMED_JSON", "Request body is not valid JSON", 400)
          : new ApiError("INVALID_BODY", "Request body could not be read", 400));
      }
    }
    next(error);
  });
};
