//! Lightning Strike API — axum + deadpool-postgres.
//!
//! Serves the same seven endpoints as the original FastAPI service with
//! identical paths, query parameters, and JSON shapes, plus `/live` which
//! serves the dashboard from the binary itself.

mod models;
mod routes;

use std::net::SocketAddr;

use anyhow::{Context, Result};
use axum::{routing::get, Router};
use deadpool_postgres::{Config, ManagerConfig, Pool, RecyclingMethod, Runtime};
use tokio_postgres::NoTls;
use tower_http::cors::{Any, CorsLayer};
use tower_http::trace::TraceLayer;
use tracing::info;

#[derive(Clone)]
pub struct AppState {
    pub pool: Pool,
}

fn pool_from_env() -> Result<Pool> {
    let get = |k: &str, d: &str| std::env::var(k).unwrap_or_else(|_| d.to_string());
    let mut cfg = Config::new();
    cfg.host = Some(get("POSTGRES_HOST", "postgres"));
    cfg.port = Some(get("POSTGRES_PORT", "5432").parse().unwrap_or(5432));
    cfg.dbname = Some(get("POSTGRES_DB", "lightning"));
    cfg.user = Some(get("POSTGRES_USER", "lightning_user"));
    cfg.password = Some(get("POSTGRES_PASSWORD", "lightning_pass"));
    cfg.application_name = Some("lightning-api".into());
    cfg.manager = Some(ManagerConfig {
        recycling_method: RecyclingMethod::Fast,
    });
    cfg.pool = Some(deadpool_postgres::PoolConfig::new(16));
    cfg.create_pool(Some(Runtime::Tokio1), NoTls)
        .context("create pool")
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,tower_http=warn".into()),
        )
        .with_target(false)
        .init();

    let state = AppState {
        pool: pool_from_env()?,
    };

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .route("/", get(routes::root))
        .route("/health", get(routes::health))
        .route("/strikes", get(routes::strikes))
        .route("/strikes/recent", get(routes::recent))
        .route("/strikes/nearby", get(routes::nearby))
        .route("/strikes/stats", get(routes::stats))
        .route("/ingestion/stats", get(routes::ingestion_stats))
        .route("/live", get(routes::live))
        .layer(cors)
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8000);
    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    info!(%addr, version = env!("CARGO_PKG_VERSION"), "lightning-api listening");

    let listener = tokio::net::TcpListener::bind(addr).await.context("bind")?;
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            tokio::signal::ctrl_c().await.ok();
        })
        .await
        .context("serve")?;
    Ok(())
}
