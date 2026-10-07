package httpserver

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/buildinfo"
)

type fakeReadinessChecker struct {
	err error
}

func (f fakeReadinessChecker) Check(context.Context) error {
	return f.err
}

func TestLive(t *testing.T) {
	req := httptest.NewRequest(
		http.MethodGet,
		"/health/live",
		nil,
	)
	rec := httptest.NewRecorder()

	NewHandler(nil).ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf(
			"expected status %d, got %d",
			http.StatusOK,
			rec.Code,
		)
	}

	var response healthResponse

	if err := json.Unmarshal(
		rec.Body.Bytes(),
		&response,
	); err != nil {
		t.Fatalf("decode response: %v", err)
	}

	if response.Status != "live" {
		t.Fatalf(
			"expected status live, got %q",
			response.Status,
		)
	}
}

func TestReady(t *testing.T) {
	req := httptest.NewRequest(
		http.MethodGet,
		"/health/ready",
		nil,
	)
	rec := httptest.NewRecorder()

	NewHandler(
		fakeReadinessChecker{},
	).ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf(
			"expected status %d, got %d",
			http.StatusOK,
			rec.Code,
		)
	}
}

func TestReadyUnavailable(t *testing.T) {
	req := httptest.NewRequest(
		http.MethodGet,
		"/health/ready",
		nil,
	)
	rec := httptest.NewRecorder()

	NewHandler(
		fakeReadinessChecker{
			err: errors.New("unavailable"),
		},
	).ServeHTTP(rec, req)

	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf(
			"expected status %d, got %d",
			http.StatusServiceUnavailable,
			rec.Code,
		)
	}
}

func TestReadyWithoutChecker(t *testing.T) {
	req := httptest.NewRequest(
		http.MethodGet,
		"/health/ready",
		nil,
	)
	rec := httptest.NewRecorder()

	NewHandler(nil).ServeHTTP(rec, req)

	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf(
			"expected status %d, got %d",
			http.StatusServiceUnavailable,
			rec.Code,
		)
	}
}

func TestVersion(t *testing.T) {
	originalVersion := buildinfo.Version
	originalGitSHA := buildinfo.GitSHA
	originalBuildTime := buildinfo.BuildTime

	t.Cleanup(func() {
		buildinfo.Version = originalVersion
		buildinfo.GitSHA = originalGitSHA
		buildinfo.BuildTime = originalBuildTime
	})

	buildinfo.Version = "test-version"
	buildinfo.GitSHA = "0123456789abcdef0123456789abcdef01234567"
	buildinfo.BuildTime = "2026-09-25T00:00:00Z"

	req := httptest.NewRequest(
		http.MethodGet,
		"/version",
		nil,
	)
	rec := httptest.NewRecorder()

	NewHandler(
		fakeReadinessChecker{},
	).ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf(
			"expected status %d, got %d",
			http.StatusOK,
			rec.Code,
		)
	}

	if got := rec.Header().Get("Cache-Control"); got != "no-store" {
		t.Fatalf("expected Cache-Control no-store, got %q", got)
	}

	var response buildinfo.Info

	if err := json.Unmarshal(
		rec.Body.Bytes(),
		&response,
	); err != nil {
		t.Fatalf("decode response: %v", err)
	}

	if response.Version != buildinfo.Version {
		t.Fatalf(
			"expected version %q, got %q",
			buildinfo.Version,
			response.Version,
		)
	}

	if response.GitSHA != buildinfo.GitSHA {
		t.Fatalf(
			"expected git SHA %q, got %q",
			buildinfo.GitSHA,
			response.GitSHA,
		)
	}
}
