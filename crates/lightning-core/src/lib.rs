//! Shared core for the lightning data pipeline: Blitzortung LZW decoding,
//! epoch recovery, the strike model, and geo helpers.

pub mod epoch;
pub mod geo;
pub mod lzw;
pub mod strike;

pub use epoch::Recovery;
pub use strike::{decode_frame, DecodeError, RawStrike, Strike};

/// Message the Blitzortung server expects to start the strike stream.
pub const SUBSCRIBE_MSG: &str = r#"{"a":111}"#;

/// Default upstream WebSocket endpoint.
pub const DEFAULT_WS_URL: &str = "wss://ws7.blitzortung.org/";
