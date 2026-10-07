package httpserver

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/buildinfo"
)

const readinessTimeout = 2 * time.Second

type ReadinessChecker interface {
	Check(context.Context) error
}

type ReadinessFunc func(context.Context) error

func (f ReadinessFunc) Check(ctx context.Context) error {
	return f(ctx)
}

type healthResponse struct {
	Status string `json:"status"`
}

func NewHandler(
	readiness ReadinessChecker,
) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /health/live", handleLive)
	mux.HandleFunc("GET /health/ready", handleReady(readiness))
	mux.HandleFunc("GET /version", handleVersion)

	return mux
}

func handleLive(
	w http.ResponseWriter,
	_ *http.Request,
) {
	writeJSON(w, http.StatusOK, healthResponse{
		Status: "live",
	})
}

func handleReady(
	readiness ReadinessChecker,
) http.HandlerFunc {
	return func(
		w http.ResponseWriter,
		r *http.Request,
	) {
		if readiness == nil {
			writeJSON(
				w,
				http.StatusServiceUnavailable,
				healthResponse{Status: "not_ready"},
			)
			return
		}

		ctx, cancel := context.WithTimeout(
			r.Context(),
			readinessTimeout,
		)
		defer cancel()

		if err := readiness.Check(ctx); err != nil {
			writeJSON(
				w,
				http.StatusServiceUnavailable,
				healthResponse{Status: "not_ready"},
			)
			return
		}

		writeJSON(w, http.StatusOK, healthResponse{
			Status: "ready",
		})
	}
}

func handleVersion(
	w http.ResponseWriter,
	_ *http.Request,
) {
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(
		w,
		http.StatusOK,
		buildinfo.Current(),
	)
}

func writeJSON(
	w http.ResponseWriter,
	status int,
	value any,
) {
	w.Header().Set(
		"Content-Type",
		"application/json; charset=utf-8",
	)
	w.WriteHeader(status)

	_ = json.NewEncoder(w).Encode(value)
}
