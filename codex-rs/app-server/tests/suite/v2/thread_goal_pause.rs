//! Native goal pause cancels only currently executing owned descendants.

use std::collections::HashMap;
use std::collections::HashSet;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::AtomicBool;
use std::sync::atomic::AtomicUsize;
use std::sync::atomic::Ordering;
use std::time::Duration;

use anyhow::Context;
use anyhow::Result;
use app_test_support::MockResponsesConfig;
use app_test_support::TestAppServer;
use axum::Json;
use axum::Router;
use axum::extract::State;
use axum::http::header;
use axum::response::IntoResponse;
use axum::routing::post;
use codex_app_server_protocol::ThreadStartParams;
use codex_app_server_protocol::TurnStartParams;
use codex_app_server_protocol::TurnStartResponse;
use codex_app_server_protocol::UserInput;
use codex_features::Feature;
use codex_protocol::ThreadId;
use codex_utils_absolute_path::test_support::PathExt;
use core_test_support::responses;
use pretty_assertions::assert_eq;
use serde_json::Value;
use serde_json::json;
use tempfile::TempDir;
use tokio::net::TcpListener;
use tokio::sync::Notify;
use tokio::sync::watch;
use tokio::time::timeout;

const TIMEOUT: Duration = Duration::from_secs(30);

struct ModelState {
    root: Mutex<String>,
    unrelated: Mutex<String>,
    running_phase: AtomicBool,
    root_requests: AtomicUsize,
    active_requests: AtomicUsize,
    idle_ids: Mutex<HashSet<String>>,
    held: Mutex<HashMap<String, String>>,
    changed: Notify,
    release: watch::Sender<bool>,
}

impl ModelState {
    fn new() -> Self {
        Self {
            root: Mutex::new(String::new()),
            unrelated: Mutex::new(String::new()),
            running_phase: AtomicBool::new(false),
            root_requests: AtomicUsize::new(0),
            active_requests: AtomicUsize::new(0),
            idle_ids: Mutex::new(HashSet::new()),
            held: Mutex::new(HashMap::new()),
            changed: Notify::new(),
            release: watch::channel(false).0,
        }
    }
}

fn completed(id: &str) -> Vec<Value> {
    vec![
        responses::ev_response_created(id),
        responses::ev_assistant_message(&format!("message-{id}"), "fixture finished"),
        responses::ev_completed_with_tokens(id, 11),
    ]
}

fn spawn(id: &str, task_name: &str) -> Vec<Value> {
    vec![
        responses::ev_response_created(id),
        responses::ev_function_call_with_namespace(
            id,
            "collaboration",
            "spawn_agent",
            &json!({"task_name": task_name, "message": "Run the isolated fixture task.", "fork_turns": "none"}).to_string(),
        ),
        responses::ev_completed_with_tokens(id, 100),
    ]
}

async fn model_response(
    State(state): State<Arc<ModelState>>,
    Json(request): Json<Value>,
) -> impl IntoResponse {
    let id = request["client_metadata"]["thread_id"]
        .as_str()
        .expect("native request thread identity")
        .to_string();
    let metadata: Value = serde_json::from_str(
        request["client_metadata"]["x-codex-turn-metadata"]
            .as_str()
            .expect("canonical turn metadata"),
    )
    .expect("valid canonical turn metadata");
    let name = metadata["agent_name"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    let root = state.root.lock().expect("root identity").clone();
    let unrelated = state.unrelated.lock().expect("unrelated identity").clone();
    let is_idle = name.ends_with("/goal_pause_idle_one") || name.ends_with("/goal_pause_idle_two");
    let events = if is_idle {
        state.idle_ids.lock().expect("idle IDs").insert(id.clone());
        state.changed.notify_one();
        Some(completed(&id))
    } else if id == root && !state.running_phase.load(Ordering::SeqCst) {
        match state.root_requests.fetch_add(1, Ordering::SeqCst) {
            0 => Some(spawn("spawn-idle-one", "goal_pause_idle_one")),
            1 => Some(spawn("spawn-idle-two", "goal_pause_idle_two")),
            _ => Some(completed("prepared-root")),
        }
    } else if id == root && state.root_requests.fetch_add(1, Ordering::SeqCst) == 0 {
        Some(spawn("spawn-active", "goal_pause_active"))
    } else if name.ends_with("/goal_pause_active")
        && state.active_requests.fetch_add(1, Ordering::SeqCst) == 0
    {
        Some(spawn("spawn-grandchild", "goal_pause_grandchild"))
    } else if id == root
        || id == unrelated
        || name.ends_with("/goal_pause_active")
        || name.ends_with("/goal_pause_grandchild")
    {
        None
    } else {
        Some(completed("unrelated-background-fixture"))
    };
    let events = match events {
        Some(events) => events,
        None => {
            let mut release = state.release.subscribe();
            state
                .held
                .lock()
                .expect("held requests")
                .insert(id.clone(), name);
            state.changed.notify_one();
            let _ = release.wait_for(|released| *released).await;
            completed(&format!("released-{id}"))
        }
    };
    (
        [(header::CONTENT_TYPE, "text/event-stream")],
        responses::sse(events),
    )
}

async fn request(app: &mut TestAppServer, method: &str, params: Value) -> Result<Value> {
    let id = app.send_raw_request(method, Some(params)).await?;
    Ok(timeout(TIMEOUT, app.read_response::<Value>(id)).await??)
}

async fn start_turn(app: &mut TestAppServer, id: &str) -> Result<()> {
    let request_id = app
        .send_turn_start_request(TurnStartParams {
            thread_id: id.to_string(),
            input: vec![UserInput::Text {
                text: "Run the local goal-pause fixture.".to_string(),
                text_elements: vec![],
            }],
            ..Default::default()
        })
        .await?;
    let _: TurnStartResponse = timeout(TIMEOUT, app.read_response(request_id)).await??;
    Ok(())
}

async fn wait_status(app: &mut TestAppServer, id: &str, status: &str) -> Result<Value> {
    timeout(TIMEOUT, async {
        loop {
            let result = request(
                app,
                "thread/read",
                json!({"threadId": id, "includeTurns": false}),
            )
            .await?;
            if result["thread"]["status"]["type"] == status {
                return Ok(result);
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .with_context(|| format!("waiting for fixture thread {id} to become {status}"))?
}

#[test_case::test_case(true; "explicit pause")]
#[test_case::test_case(false; "objective edit of already paused goal")]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn native_goal_pause_scopes_worker_interruption(pause: bool) -> Result<()> {
    let state = Arc::new(ModelState::new());
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let uri = format!("http://{}", listener.local_addr()?);
    let router = Router::new()
        .route("/v1/responses", post(model_response))
        .with_state(Arc::clone(&state));
    let server = tokio::spawn(async move { axum::serve(listener, router).await });
    let home = TempDir::new()?;
    MockResponsesConfig::new(&uri)
        .with_model("gpt-6.1-sol")
        .with_provider_config("supports_websockets = false")
        .enable_feature(Feature::Goals)
        .enable_feature(Feature::Collab)
        .disable_feature(Feature::EnableRequestCompression)
        // Three resident child slots leave one idle worker loaded and evict the other.
        .with_extra_config(
            "[features.multi_agent_v2]\nenabled = true\nmax_concurrent_threads_per_session = 4",
        )
        .write(home.path())?;
    let mut app = TestAppServer::builder()
        .with_codex_home(home.path())
        .without_managed_config()
        .build_initialized()
        .await?;
    let root = app
        .start_thread(ThreadStartParams::default())
        .await?
        .thread
        .id;
    *state.root.lock().expect("root identity") = root.clone();
    let unrelated = app
        .start_thread(ThreadStartParams::default())
        .await?
        .thread
        .id;
    *state.unrelated.lock().expect("unrelated identity") = unrelated.clone();

    start_turn(&mut app, &root).await?;
    timeout(TIMEOUT, async {
        loop {
            if state.idle_ids.lock().expect("idle IDs").len() == 2 {
                break;
            }
            state.changed.notified().await;
        }
    })
    .await
    .context("waiting for two completed fixture children")?;
    let idle: Vec<String> = state
        .idle_ids
        .lock()
        .expect("idle IDs")
        .iter()
        .cloned()
        .collect();
    for id in &idle {
        wait_status(&mut app, id, "idle").await?;
    }
    wait_status(&mut app, &root, "idle").await?;
    state.root_requests.store(0, Ordering::SeqCst);
    state.running_phase.store(true, Ordering::SeqCst);
    start_turn(&mut app, &unrelated).await?;
    request(&mut app, "thread/goal/set", json!({"threadId": root, "objective": "Hold the goal fixture", "status": if pause {"active"} else {"paused"}, "tokenBudget": 10000, "origin": "user"})).await?;
    if !pause {
        start_turn(&mut app, &root).await?;
    }
    timeout(TIMEOUT, async {
        loop {
            if state.held.lock().expect("held requests").len() == 4 {
                break;
            }
            state.changed.notified().await;
        }
    })
    .await
    .with_context(|| {
        format!(
            "waiting for the root, unrelated thread, child and grandchild; held: {:?}",
            state.held.lock().expect("held requests")
        )
    })?;
    let descendants: Vec<String> = state
        .held
        .lock()
        .expect("held requests")
        .keys()
        .filter(|id| **id != root && **id != unrelated)
        .cloned()
        .collect();
    assert_eq!(descendants.len(), 2);
    let loaded_before = request(&mut app, "thread/loaded/list", json!({})).await?;
    let unloaded_idle: Vec<&String> = idle
        .iter()
        .filter(|id| {
            !loaded_before["data"]
                .as_array()
                .expect("loaded IDs")
                .contains(&json!(id))
        })
        .collect();
    assert_eq!(unloaded_idle.len(), 1, "one idle descendant is evicted");
    let mut idle_history = HashMap::new();
    for id in &idle {
        idle_history.insert(
            id.clone(),
            request(
                &mut app,
                "thread/read",
                json!({"threadId": id, "includeTurns": true}),
            )
            .await?["thread"]["turns"]
                .clone(),
        );
        assert!(
            !idle_history[id].as_array().expect("idle turns").is_empty(),
            "history preservation must compare actual saved turns"
        );
    }
    let mut descendant_turns = HashMap::new();
    for id in &descendants {
        let before = request(
            &mut app,
            "thread/read",
            json!({"threadId": id, "includeTurns": true}),
        )
        .await?;
        let turns = before["thread"]["turns"].as_array().expect("active turns");
        assert_eq!(turns.last().expect("active turn")["status"], "inProgress");
        descendant_turns.insert(
            id.clone(),
            turns
                .iter()
                .map(|turn| turn["id"].clone())
                .collect::<Vec<_>>(),
        );
    }
    let database = codex_state::StateRuntime::init(
        codex_state::SqliteConfig::new_for_testing(home.path().abs()),
        "mock_provider".to_string(),
    )
    .await?;
    let root_id = ThreadId::from_string(&root)?;
    let before = database
        .thread_goals()
        .get_thread_goal(root_id)
        .await?
        .expect("goal exists");
    let mutation = if pause {
        json!({"threadId": root, "status": "paused", "origin": "user"})
    } else {
        json!({"threadId": root, "objective": "Edited paused objective", "origin": "user"})
    };
    request(&mut app, "thread/goal/set", mutation).await?;
    for id in &descendants {
        wait_status(&mut app, id, if pause { "idle" } else { "active" }).await?;
    }
    wait_status(&mut app, &root, "active").await?;
    wait_status(&mut app, &unrelated, "active").await?;
    let loaded_after = request(&mut app, "thread/loaded/list", json!({})).await?;
    for id in unloaded_idle {
        assert!(
            !loaded_after["data"]
                .as_array()
                .expect("loaded IDs")
                .contains(&json!(id)),
            "pause must not load saved descendants"
        );
    }
    for id in &idle {
        let after = request(
            &mut app,
            "thread/read",
            json!({"threadId": id, "includeTurns": true}),
        )
        .await?;
        assert_eq!(after["thread"]["id"], json!(id));
        assert_eq!(after["thread"]["turns"], idle_history[id]);
    }
    for id in &descendants {
        let after = request(
            &mut app,
            "thread/read",
            json!({"threadId": id, "includeTurns": true}),
        )
        .await?;
        let turns = after["thread"]["turns"].as_array().expect("retained turns");
        assert_eq!(
            turns
                .iter()
                .map(|turn| turn["id"].clone())
                .collect::<Vec<_>>(),
            descendant_turns[id],
            "pause must retain the exact descendant turn identities"
        );
        assert_eq!(
            turns.last().expect("retained turn")["status"],
            if pause { "interrupted" } else { "inProgress" }
        );
        assert!(
            database
                .get_thread(ThreadId::from_string(id)?)
                .await?
                .is_some(),
            "interruption must retain the descendant identity/history"
        );
    }
    let after = database
        .thread_goals()
        .get_thread_goal(root_id)
        .await?
        .expect("same goal remains");
    assert_eq!(after.goal_id, before.goal_id);
    assert_eq!(after.token_budget, before.token_budget);
    assert!(after.tokens_used >= before.tokens_used);
    assert!(after.time_used_seconds >= before.time_used_seconds);
    assert_eq!(after.status, codex_state::ThreadGoalStatus::Paused);
    state.release.send_replace(true);
    assert!(
        timeout(TIMEOUT, app.shutdown_gracefully())
            .await??
            .success()
    );
    server.abort();
    Ok(())
}
