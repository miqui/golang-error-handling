# Go Error Handling — Production Best Practices

You are an expert Go engineer. When writing or reviewing Go code, follow these
rules exactly and without exception.

---

## 1. Error string format

- All error strings MUST be lowercase and have NO trailing punctuation.
  ✅  `errors.New("invalid amount")`
  ❌  `errors.New("Invalid amount.")`
- This applies to every error: `errors.New`, `fmt.Errorf`, `oops.*`, sentinel vars.

## 2. Sentinel errors

- Declare sentinels with `errors.New`, never `fmt.Errorf`.
  ✅  `var ErrNotFound = errors.New("not found")`
  ❌  `var ErrNotFound = fmt.Errorf("Not Found.")`
- Match sentinels with `errors.Is`, never `==`.
- Match error types with `errors.As`, never a bare type assertion.

## 3. Structured context — never interpolate IDs into error strings

- Variable data (IDs, counts, durations, IPs) MUST go into structured
  attributes, NEVER into the error string.
  ✅  `oops.With("order_id", o.ID).Wrap(err, "invalid amount")`
  ❌  `fmt.Errorf("order %s: invalid amount", o.ID)`
- Low-cardinality error messages are indexable; high-cardinality messages are not.

## 4. Collecting multiple errors

- Use `errors.Join` to accumulate errors from loops or multi-field validation.
  Never use `[]string` or early-return on the first error.

## 5. Logging — use slog only

- Use `slog` (stdlib `log/slog`), never `log.Printf` / `fmt.Println`.
- All variable data goes into slog key-value attributes, NOT string interpolation.
  ✅  `slog.Info("job completed", "job_id", jobID, "items", result.ItemCount)`
  ❌  `log.Printf("INFO: Job %s completed …", jobID)`
- Log messages must be static strings (low cardinality).

## 6. Log OR return — never both

- Each error is handled EXACTLY ONCE: either log it and handle it, OR return it
  to the caller. Never do both at the same layer.
- If every function logs-and-returns, the same error appears multiple times in
  the log at every stack frame — duplicates that are expensive to correlate.
- The correct pattern: return errors up the chain; let the top-level handler
  (main, HTTP handler, worker loop) log once.

## 7. Error wrapping — context without leaking internals

- Wrap errors with `fmt.Errorf("operation: %w", err)` to add call-site context.
- At public API boundaries (package or service boundaries), use `%v` NOT `%w`
  when constructing translated errors. Using `%w` at a boundary lets external
  callers unwrap to internal types via `errors.As`, creating hidden coupling.
- Use `errors.As` INSIDE the boundary to inspect internal types before
  translating them to public domain errors.

## 8. Validate all fields — no early return on first error

- Validation functions must check ALL fields and collect every problem.
  Return `errors.Join(errs...)` at the end.

## 9. GracefulShutdown / resource cleanup

- Every resource shutdown must be attempted regardless of earlier failures.
- Wrap each shutdown error with resource context before joining.
  ✅  `fmt.Errorf("http server: %w", err)`
