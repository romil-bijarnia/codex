#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DangerousCommandPlatform {
    Posix,
    Windows,
}

impl DangerousCommandPlatform {
    pub fn host() -> Self {
        if cfg!(windows) {
            Self::Windows
        } else {
            Self::Posix
        }
    }
}

pub fn powershell_commands_are_independent(script: &str, commands: &[Vec<String>]) -> bool {
    !script.contains('|')
        && commands.iter().all(|words| {
            !matches!(
                words
                    .first()
                    .and_then(|word| executable_basename(word))
                    .as_deref(),
                Some(
                    "set-alias"
                        | "sal"
                        | "new-alias"
                        | "nal"
                        | "set-item"
                        | "si"
                        | "new-item"
                        | "ni"
                )
            ) && !words.iter().skip(1).any(|word| {
                let word = word.to_ascii_lowercase();
                [
                    "start-process",
                    "invoke-item",
                    "shellexecute",
                    "shell.application",
                ]
                .iter()
                .any(|marker| word.contains(marker))
            })
        })
}

fn executable_basename(executable: &str) -> Option<String> {
    let name = executable
        .rsplit(['/', '\\'])
        .next()
        .filter(|name| !name.is_empty())?;
    let name = match name.as_bytes() {
        [drive, b':', ..] if drive.is_ascii_alphabetic() => &name[2..],
        _ => name,
    };
    (!name.is_empty()).then(|| name.to_ascii_lowercase())
}
