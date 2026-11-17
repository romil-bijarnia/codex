//! Forward explicit programs while leaving entitlement and model policy to the server.

use codex_protocol::turn_input::CyberAccessProgram;
use std::sync::Arc;

/// Host requirement for every model request issued by this thread.
///
/// Install in thread extension data before startup and reapply on cold resume.
/// Core snapshots it before extension callbacks; it is not inherited or persisted.
/// Every request must resolve to one listed program; backend authorization remains authoritative.
#[derive(Clone, Debug)]
pub struct RequiredCyberAccessPrograms {
    pub allowed: Vec<CyberAccessProgram>,
}

pub(crate) struct RequiredCyberAccessProgramsSnapshot(pub Arc<RequiredCyberAccessPrograms>);

impl RequiredCyberAccessPrograms {
    /// Read Core's startup snapshot, independent of later edits to the public attachment.
    pub fn for_thread(data: &codex_extension_api::ExtensionData) -> Option<Arc<Self>> {
        data.get::<RequiredCyberAccessProgramsSnapshot>()
            .map(|snapshot| Arc::clone(&snapshot.0))
    }
}
