package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/buildinfo"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/database"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/httpserver"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/migrations"
)

const databaseStartupTimeout = 10 * time.Second

func main() {
	os.Exit(run())
}

func run() int {
	cfg, err := config.Load()
	if err != nil {
		slog.New(slog.NewJSONHandler(os.Stdout, nil)).Error(
			"invalid configuration",
			"error",
			err,
		)
		return 2
	}

	logger := slog.New(slog.NewJSONHandler(
		os.Stdout,
		&slog.HandlerOptions{Level: slogLevel(cfg.LogLevel)},
	))
	slog.SetDefault(logger)

	if cfg.AppEnv != config.AppEnvDevelopment {
		if err := buildinfo.ValidateRelease(cfg.BuildRevision); err != nil {
			slog.Error("invalid release identity", "error", err)
			return 2
		}
	}

	startupCtx, startupCancel := context.WithTimeout(
		context.Background(),
		databaseStartupTimeout,
	)

	dbPool, err := database.Open(
		startupCtx,
		cfg.DatabaseURL,
	)
	startupCancel()

	if err != nil {
		slog.Error(
			"database startup check failed",
			"error",
			err,
		)
		return 1
	}
	defer dbPool.Close()

	readiness := httpserver.ReadinessFunc(func(ctx context.Context) error {
		if err := dbPool.Ping(ctx); err != nil {
			return err
		}

		return migrations.New(dbPool).CheckReady(ctx)
	})

	server := &http.Server{
		Addr:              cfg.HTTPListenAddr,
		Handler:           httpserver.NewHandler(readiness),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	errCh := make(chan error, 1)

	go func() {
		slog.Info(
			"server starting",
			"address", cfg.HTTPListenAddr,
			"version", buildinfo.Version,
			"git_sha", buildinfo.GitSHA,
			"build_time", buildinfo.BuildTime,
		)

		err := server.ListenAndServe()
		if err != nil &&
			!errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
	}()

	signalCh := make(chan os.Signal, 1)
	signal.Notify(
		signalCh,
		syscall.SIGINT,
		syscall.SIGTERM,
	)
	defer signal.Stop(signalCh)

	select {
	case sig := <-signalCh:
		slog.Info(
			"shutdown signal received",
			"signal",
			sig.String(),
		)

	case err := <-errCh:
		slog.Error(
			"server failed",
			"error",
			err,
		)
		return 1
	}

	ctx, cancel := context.WithTimeout(
		context.Background(),
		cfg.ShutdownTimeout,
	)
	defer cancel()

	if err := server.Shutdown(ctx); err != nil {
		slog.Error(
			"graceful shutdown failed",
			"error",
			err,
		)
		return 1
	}

	slog.Info("server stopped")
	return 0
}

func slogLevel(level config.LogLevel) slog.Level {
	switch level {
	case config.LogLevelDebug:
		return slog.LevelDebug
	case config.LogLevelWarn:
		return slog.LevelWarn
	case config.LogLevelError:
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
