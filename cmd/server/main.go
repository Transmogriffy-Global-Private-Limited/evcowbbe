package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/api"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/auth"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/database"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	c, err := config.Load()
	if err != nil {
		logger.Error("configuration invalid", "reason", err.Error())
		os.Exit(1)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	db, err := database.Open(ctx, c.DatabaseURL, c.DBMaxConns)
	if err != nil {
		logger.Error("database unavailable")
		os.Exit(1)
	}
	defer db.Close()
	if err = database.VerifySchema(ctx, db); err != nil {
		logger.Error("database schema missing or drifted")
		os.Exit(1)
	}
	// Server startup never applies migrations automatically.
	authService := auth.New(db, c)
	go authService.Outbox.Run(ctx, logger)
	server := &http.Server{Addr: c.Address, Handler: (&api.Server{Auth: authService, DB: db, C: c, Logger: logger}).Handler(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 16 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16384}
	go func() {
		<-ctx.Done()
		ctx, stop := context.WithTimeout(context.Background(), 8*time.Second)
		defer stop()
		_ = server.Shutdown(ctx)
	}()
	logger.Info("auth API listening", "address", c.Address, "environment", c.Env)
	if err = server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		logger.Error("server stopped unexpectedly", "type", "listener_failure")
		os.Exit(1)
	}
}
