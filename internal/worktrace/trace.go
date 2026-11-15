// Package worktrace records opt-in work at the product's actual boundaries.
// It carries no input authority, cache policy, document text or telemetry client.
package worktrace

import (
	"context"
	"encoding/json"
	"io"
	"os"
	"sync"
	"sync/atomic"
	"time"
)

var enabled = os.Getenv("ARGO_WORKFLOWS_LS_TRACE") == "1"

type Counter uint8

const (
	ReadAttempts Counter = iota
	ReadFiles
	ReadBytes
	ReadFailures
	ParseCalls
	ParseBytes
	ParseSourceCalls
	ParseRecoveryCalls
	ParseSyntheticCalls
	ParsePreparseCalls
	ParseHelmCalls
	ProjectionCalls
	ConfigDecodes
	SchemaLoadCalls
	SchemaLoads
	SchemaLoadHits
	SchemaCompileCalls
	SchemaCompiles
	SchemaCompileHits
	SchemaCompileWaitNanoseconds
	SchemaQueries
	SchemaLookups
	SchemaTypes
	SchemaValues
	SchemaAllowsKey
	SchemaSelectShapes
	DomainPasses
	DomainResources
	DomainLocalPasses
	DomainExternalPasses
	DomainBindingPasses
	ViewLookups
	ViewHits
	ProjectionLookups
	ProjectionHits
	FactLookups
	FactsRestored
	ProjectionsRestored
	ResultCacheLookups
	ResultCacheHits
	ExternalFactLookups
	ExternalFactHits
	counterCount
)

var names = [...]string{
	"readAttempts", "readFiles", "readBytes", "readFailures",
	"parseCalls", "parseBytes", "parseSourceCalls", "parseRecoveryCalls", "parseSyntheticCalls", "parsePreparseCalls", "parseHelmCalls",
	"projectionCalls", "configDecodes", "schemaLoadCalls", "schemaLoads", "schemaLoadHits",
	"schemaCompileCalls", "schemaCompiles", "schemaCompileHits", "schemaCompileWaitNanoseconds",
	"schemaQueries", "schemaLookups", "schemaTypes", "schemaValues", "schemaAllowsKey", "schemaSelectShapes",
	"domainPasses", "domainResources", "domainLocalPasses", "domainExternalPasses", "domainBindingPasses",
	"viewLookups", "viewHits", "projectionLookups", "projectionHits", "factLookups", "factsRestored", "projectionsRestored",
	"resultCacheLookups", "resultCacheHits", "externalFactLookups", "externalFactHits",
}

type key struct{}

// A disabled counter returns before context lookup, allocation or clock access.
func Add(ctx context.Context, counter Counter, amount int64) {
	if !enabled || amount == 0 || ctx == nil {
		return
	}
	if span, _ := ctx.Value(key{}).(*Span); span != nil {
		span.work[counter].Add(amount)
	}
}

func Parse(ctx context.Context, purpose Counter, bytes int) {
	Add(ctx, ParseCalls, 1)
	Add(ctx, ParseBytes, int64(bytes))
	Add(ctx, purpose, 1)
	if !enabled || ctx == nil {
		return
	}
	// Dispatch can be cancelled before any parser boundary is entered. Expose
	// the first actual submitted parse separately, without adding work or
	// changing parser scheduling. Counts describe submitted inputs, not parser
	// completion, including when cancellation arrives during this call.
	if span, _ := ctx.Value(key{}).(*Span); span != nil && span.origin == "request" && span.parseStarted.CompareAndSwap(false, true) {
		value := span.base("parse-start")
		value["purpose"], value["inputBytes"] = names[purpose], bytes
		span.emit(value)
	}
}

func SchemaQuery(ctx context.Context, operation Counter) {
	Add(ctx, SchemaQueries, 1)
	Add(ctx, operation, 1)
}

func Now(ctx context.Context) time.Time {
	if !enabled || ctx == nil {
		return time.Time{}
	}
	if span, _ := ctx.Value(key{}).(*Span); span != nil {
		return time.Now()
	}
	return time.Time{}
}

func Elapsed(ctx context.Context, counter Counter, start time.Time) {
	if !start.IsZero() {
		Add(ctx, counter, time.Since(start).Nanoseconds())
	}
}

type lockedWriter struct {
	mu sync.Mutex
	w  io.Writer
}

func (w *lockedWriter) Write(data []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.w.Write(data)
}

// Writer keeps opt-in background records and the existing owner log frames
// whole. It does not add a queue or retain records in the server.
func Writer(w io.Writer) io.Writer {
	if !enabled {
		return w
	}
	return &lockedWriter{w: w}
}

type Tracer struct {
	w        io.Writer
	sequence atomic.Uint64
}

func New(w io.Writer) *Tracer {
	if !enabled {
		return nil
	}
	return &Tracer{w: w}
}

type Span struct {
	tracer                  *Tracer
	sequence                uint64
	origin, method, outcome string
	id                      json.RawMessage
	work                    [counterCount]atomic.Int64
	parseStarted            atomic.Bool
	mu                      sync.Mutex
	admitted, started, done time.Time
	cancelled, replied      time.Time
}

func (t *Tracer) Begin(origin, method string, id json.RawMessage) *Span {
	if t == nil {
		return nil
	}
	return &Span{tracer: t, sequence: t.sequence.Add(1), origin: origin, method: method,
		id: append(json.RawMessage(nil), id...), admitted: time.Now()}
}

func (s *Span) Context(ctx context.Context) context.Context {
	if s == nil {
		return ctx
	}
	return context.WithValue(ctx, key{}, s)
}

func (s *Span) base(event string) map[string]any {
	value := map[string]any{"phase": "work", "origin": s.origin, "sequence": s.sequence, "event": event}
	if s.method != "" {
		value["method"] = s.method
	}
	if len(s.id) != 0 {
		value["requestId"] = s.id
	}
	return value
}

func (s *Span) emit(value map[string]any) {
	data, err := json.Marshal(value)
	if err == nil {
		_, _ = s.tracer.w.Write(append(data, '\n'))
	}
}

func (s *Span) Start() {
	if s == nil {
		return
	}
	s.mu.Lock()
	s.started = time.Now()
	s.mu.Unlock()
	// Only foreground dispatch needs a live start record for cancellation and
	// queue measurements. Background work is retained in its final record.
	if s.origin == "request" {
		s.emit(s.base("start"))
	}
}

func (s *Span) Done() {
	if s != nil {
		s.mu.Lock()
		s.done = time.Now()
		s.mu.Unlock()
	}
}

func (s *Span) Cancel() {
	if s != nil {
		s.mu.Lock()
		if s.cancelled.IsZero() {
			s.cancelled = time.Now()
		}
		s.mu.Unlock()
	}
}

func (s *Span) Reply(outcome string) {
	if s != nil {
		s.mu.Lock()
		if s.replied.IsZero() {
			s.replied, s.outcome = time.Now(), outcome
		}
		s.mu.Unlock()
	}
}

// Finish runs when the existing owner releases the worker slot, including a
// worker whose response was cancelled or discarded. Counters are never inferred
// from a delivered result and are not process-global before/after deltas.
func (s *Span) Finish(outcome string) {
	if s == nil {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now()
	value := s.base("finish")
	if s.outcome != "" {
		outcome = s.outcome
	}
	value["outcome"] = outcome
	value["releaseNanoseconds"] = now.Sub(s.admitted).Nanoseconds()
	if !s.started.IsZero() {
		value["queueNanoseconds"] = s.started.Sub(s.admitted).Nanoseconds()
		if !s.done.IsZero() {
			value["serviceNanoseconds"] = s.done.Sub(s.started).Nanoseconds()
		}
	}
	if !s.replied.IsZero() {
		value["replyNanoseconds"] = s.replied.Sub(s.admitted).Nanoseconds()
	}
	if !s.cancelled.IsZero() {
		value["cancelToReleaseNanoseconds"] = now.Sub(s.cancelled).Nanoseconds()
		if !s.replied.IsZero() {
			value["cancelToReplyNanoseconds"] = s.replied.Sub(s.cancelled).Nanoseconds()
		}
	}
	work := map[string]int64{}
	for counter, name := range names {
		if amount := s.work[counter].Load(); amount != 0 {
			work[name] = amount
		}
	}
	value["work"] = work
	s.emit(value)
}
