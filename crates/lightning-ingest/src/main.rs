//! Blitzortung ingestion service.
//!
//! Architecture:
//!
//! ```text
//!   WebSocket ──► reader task ──► mpsc(2048) ──► writer task ──► Postgres
//!                (LZW decode,                    (batches of up to
//!                 validate)                       BATCH_MAX or every
//!                                                 FLUSH_MS, one
//!                                                 multi-row INSERT)
//! ```
//!
//! Decode happens on the reader so a slow database never stalls the socket;
//! the bounded channel applies backpressure instead of unbounded growth.
//! The reader reconnects with exponential backoff on any socket error.

mod db;

use std::time::Duration;

use anyhow::{Context, Result};
use chrono::Utc;
use futures_util::{SinkExt, StreamExt};
use lightning_core::{decode_frame, Recovery, Strike, DEFAULT_WS_URL, SUBSCRIBE_MSG};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;
use tracing::{error, info, warn};

const BATCH_MAX: usize = 50;
const FLUSH_MS: u64 = 250;
const CHANNEL_CAP: usize = 2048;
const STATS_LOG_EVERY: u64 = 100;

#[derive(Default, Debug)]
struct Counters {
    received: u64,
    failed: u64,
    recovered: u64,
    no_geometry: u64,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .with_target(false)
        .init();

    info!(
        version = env!("CARGO_PKG_VERSION"),
        "lightning-ingest starting"
    );

    // Pin the TLS crypto backend explicitly; rustls refuses to guess when
    // more than one provider is compiled in via transitive features.
    rustls::crypto::ring::default_provider()
        .install_default()
        .expect("install rustls ring provider");

    let cfg = db::DbConfig::from_env();
    let client = db::connect(&cfg).await?;
    db::ensure_schema(&client).await?;

    let ws_url = std::env::var("BLITZ_WS_URL").unwrap_or_else(|_| DEFAULT_WS_URL.to_string());

    let (tx, rx) = mpsc::channel::<Result<Strike, ()>>(CHANNEL_CAP);

    let writer = tokio::spawn(writer_task(client, rx));
    let reader = tokio::spawn(reader_loop(ws_url, tx));

    tokio::select! {
        r = writer => { error!("writer task exited: {r:?}"); }
        r = reader => { error!("reader task exited: {r:?}"); }
        _ = tokio::signal::ctrl_c() => { info!("shutdown signal received"); }
    }
    Ok(())
}

/// Connect → subscribe → stream frames → decode → send. Reconnects forever.
async fn reader_loop(url: String, tx: mpsc::Sender<Result<Strike, ()>>) {
    let mut backoff = Duration::from_secs(1);
    let mut counters = Counters::default();

    loop {
        match run_socket(&url, &tx, &mut counters).await {
            Ok(()) => warn!("websocket closed cleanly; reconnecting"),
            Err(e) => warn!(error = %e, "websocket error; reconnecting in {backoff:?}"),
        }
        if tx.is_closed() {
            return;
        }
        tokio::time::sleep(backoff).await;
        backoff = (backoff * 2).min(Duration::from_secs(30));
    }
}

async fn run_socket(
    url: &str,
    tx: &mpsc::Sender<Result<Strike, ()>>,
    c: &mut Counters,
) -> Result<()> {
    let (ws, _) = tokio_tungstenite::connect_async(url)
        .await
        .context("ws connect")?;
    let (mut sink, mut stream) = ws.split();
    info!(url, "websocket connected");

    sink.send(Message::Text(SUBSCRIBE_MSG.into()))
        .await
        .context("subscribe")?;
    info!("subscribed to strike feed");

    while let Some(msg) = stream.next().await {
        let frame: String = match msg.context("ws read")? {
            Message::Text(t) => t.to_string(),
            Message::Binary(b) => String::from_utf8_lossy(&b).into_owned(),
            Message::Ping(p) => {
                sink.send(Message::Pong(p)).await.ok();
                continue;
            }
            Message::Close(_) => return Ok(()),
            _ => continue,
        };

        c.received += 1;
        match decode_frame(&frame, Utc::now()) {
            Ok((strike, rec)) => {
                if let Recovery::Rescaled { power } = rec {
                    c.recovered += 1;
                    warn!(power, total = c.recovered, "epoch recovery fired");
                }
                if strike.azimuthal_gap_deg.is_none() {
                    c.no_geometry += 1;
                }
                if tx.send(Ok(strike)).await.is_err() {
                    return Ok(());
                }
            }
            Err(e) => {
                c.failed += 1;
                warn!(error = %e, "frame rejected");
                if tx.send(Err(())).await.is_err() {
                    return Ok(());
                }
            }
        }

        if c.received.is_multiple_of(STATS_LOG_EVERY) {
            let ok = c.received - c.failed;
            let pct = ok as f64 / c.received as f64 * 100.0;
            let rec = if c.recovered > 0 {
                format!(" | Recovered: {}", c.recovered)
            } else {
                String::new()
            };
            let geo = if c.no_geometry > 0 {
                format!(" | NoGeometry: {}", c.no_geometry)
            } else {
                String::new()
            };
            info!(
                "Processed: {} | Decoded: {} | Failed: {} | Success: {pct:.1}%{rec}{geo}",
                c.received, ok, c.failed
            );
        }
    }
    Ok(())
}

/// Drain the channel into Postgres in batches.
async fn writer_task(
    mut client: tokio_postgres::Client,
    mut rx: mpsc::Receiver<Result<Strike, ()>>,
) {
    let mut batch: Vec<Strike> = Vec::with_capacity(BATCH_MAX);
    let mut failed_in_window: i32 = 0;
    let mut ticker = tokio::time::interval(Duration::from_millis(FLUSH_MS));
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

    loop {
        tokio::select! {
            item = rx.recv() => {
                match item {
                    Some(Ok(s)) => {
                        batch.push(s);
                        if batch.len() >= BATCH_MAX {
                            flush(&mut client, &mut batch, &mut failed_in_window).await;
                        }
                    }
                    Some(Err(())) => failed_in_window += 1,
                    None => {
                        flush(&mut client, &mut batch, &mut failed_in_window).await;
                        info!("channel closed; writer exiting");
                        return;
                    }
                }
            }
            _ = ticker.tick() => {
                if !batch.is_empty() || failed_in_window > 0 {
                    flush(&mut client, &mut batch, &mut failed_in_window).await;
                }
            }
        }
    }
}

async fn flush(client: &mut tokio_postgres::Client, batch: &mut Vec<Strike>, failed: &mut i32) {
    let n = batch.len() as i32;
    let stored = match db::insert_batch(client, batch).await {
        Ok(rows) => rows as i32,
        Err(e) => {
            error!(error = %e, rows = n, "batch insert failed; dropping batch");
            0
        }
    };
    let insert_failed = n - stored;
    if let Err(e) = db::bump_stats(client, n + *failed, stored, *failed + insert_failed).await {
        error!(error = %e, "stats update failed");
    }
    batch.clear();
    *failed = 0;
}
