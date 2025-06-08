//! Web Request Queuing System: 5 Parallel WebSocket-Based Queues
//! Features: Queue management, WebSocket handling, rate-limited requests, retries, SQLite logging

use actix::prelude::*;
use actix_web::{web, App, Error, HttpRequest, HttpResponse, HttpServer};
use actix_web_actors::ws;
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use uuid::Uuid;

// --- Constants ---
const QUEUE_NAMES: [&str; 5] = ["line1", "line2", "line3", "line4", "line5"];
const MAX_USERS_PER_QUEUE: usize = 5;
const MIN_REQUEST_INTERVAL: Duration = Duration::from_secs(20);

// --- Types ---
#[derive(Debug, Clone, Serialize, Deserialize)]
struct UserParams {
    // Add user parameter fields as needed
    value: String,
}

#[derive(Debug, Clone)]
struct User {
    websocket_id: String,
    parameters: UserParams,
    addr: Addr<WsSession>,
}

#[derive(Debug)]
struct QueueState {
    users: VecDeque<User>,
    last_request_time: Option<Instant>,
}

type SharedQueues = Arc<Mutex<HashMap<String, QueueState>>>;

// --- SQLite Setup ---
fn setup_db() -> Connection {
    let conn = Connection::open("queue.db").expect("Failed to open DB");
    conn.execute_batch(
        "
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            websocket_id TEXT UNIQUE NOT NULL,
            queue_name TEXT NOT NULL CHECK(queue_name IN ('line1', 'line2', 'line3', 'line4', 'line5')),
            position INTEGER NOT NULL,
            parameters TEXT,
            connected_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS queue_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_name TEXT NOT NULL CHECK(queue_name IN ('line1', 'line2', 'line3', 'line4', 'line5')),
            last_request_time DATETIME
        );
        CREATE TABLE IF NOT EXISTS request_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER,
            request_data TEXT,
            response_data TEXT,
            attempt INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        ",
    )
    .unwrap();
    conn
}

// --- WebSocket Session Actor ---
struct WsSession {
    id: String,
    queues: SharedQueues,
    db: Arc<Mutex<Connection>>,
    queue_name: Option<String>,
    params: Option<UserParams>,
}

impl Actor for WsSession {
    type Context = ws::WebsocketContext<Self>;

    fn started(&mut self, ctx: &mut Self::Context) {
        // Assign user to a queue
        let mut queues = self.queues.lock().unwrap();
        let mut min_len = MAX_USERS_PER_QUEUE + 1;
        let mut chosen_queue = None;
        for name in QUEUE_NAMES.iter() {
            let q = queues.entry(name.to_string()).or_insert(QueueState {
                users: VecDeque::new(),
                last_request_time: None,
            });
            if q.users.len() < min_len {
                min_len = q.users.len();
                chosen_queue = Some(name.to_string());
            }
        }
        if let Some(qname) = chosen_queue {
            if min_len >= MAX_USERS_PER_QUEUE {
                ctx.text("All queues are full. Please try again later.");
                ctx.close(None);
                return;
            }
            self.queue_name = Some(qname.clone());
            let params = self.params.clone().unwrap_or(UserParams {
                value: "default".to_string(),
            });
            let user = User {
                websocket_id: self.id.clone(),
                parameters: params.clone(),
                addr: ctx.address(),
            };
            queues.get_mut(&qname).unwrap().users.push_back(user.clone());
            drop(queues);

            // Insert into DB
            let db = self.db.clone();
            let websocket_id = self.id.clone();
            let queue_name = qname.clone();
            let parameters = serde_json::to_string(&params).unwrap();
            actix_web::rt::spawn(async move {
                let conn = db.lock().unwrap();
                let _ = conn.execute(
                    "INSERT INTO users (websocket_id, queue_name, position, parameters) VALUES (?1, ?2, ?3, ?4)",
                    params![websocket_id, queue_name, 0, parameters],
                );
            });

            ctx.text(format!("Joined queue: {}", qname));
            broadcast_positions(&self.queues, &qname);
        } else {
            ctx.text("All queues are full. Please try again later.");
            ctx.close(None);
        }
    }

    fn stopped(&mut self, _ctx: &mut Self::Context) {
        // Remove user from queue
        if let Some(qname) = &self.queue_name {
            let mut queues = self.queues.lock().unwrap();
            if let Some(q) = queues.get_mut(qname) {
                q.users.retain(|u| u.websocket_id != self.id);
            }
            drop(queues);

            // Remove from DB
            let db = self.db.clone();
            let websocket_id = self.id.clone();
            actix::spawn(async move {
                let conn = db.lock().unwrap();
                let _ = conn.execute(
                    "DELETE FROM users WHERE websocket_id = ?1",
                    params![websocket_id],
                );
            });

            broadcast_positions(&self.queues, qname);
        }
    }
}

impl StreamHandler<Result<ws::Message, ws::ProtocolError>> for WsSession {
    fn handle(&mut self, msg: Result<ws::Message, ws::ProtocolError>, ctx: &mut ws::WebsocketContext<Self>) {
        match msg {
            Ok(ws::Message::Text(text)) => {
                // Accept user parameters on first message
                if self.params.is_none() {
                    if let Ok(params) = serde_json::from_str::<UserParams>(&text) {
                        self.params = Some(params);
                        ctx.text("Parameters received.");
                    } else {
                        ctx.text("Invalid parameters. Please send JSON.");
                    }
                } else {
                    ctx.text("Already received parameters.");
                }
            }
            Ok(ws::Message::Ping(msg)) => ctx.pong(&msg),
            Ok(ws::Message::Pong(_)) => {}
            Ok(ws::Message::Close(_)) => {
                ctx.stop();
            }
            _ => {}
        }
    }
}

// --- Broadcast Positions ---
fn broadcast_positions(queues: &SharedQueues, queue_name: &str) {
    let queues = queues.lock().unwrap();
    if let Some(q) = queues.get(queue_name) {
        for (i, user) in q.users.iter().enumerate() {
            let msg = format!("Position in {}: {}", queue_name, i + 1);
            user.addr.do_send(WsMessage(msg));
        }
    }
}

// --- WebSocket Message ---
#[derive(Message)]
#[rtype(result = "()")]
struct WsMessage(String);

impl Handler<WsMessage> for WsSession {
    type Result = ();

    fn handle(&mut self, msg: WsMessage, ctx: &mut ws::WebsocketContext<Self>) {
        ctx.text(msg.0);
    }
}

// --- Request Processing Arbiter ---
fn start_request_processor(queues: SharedQueues, db: Arc<Mutex<Connection>>, endpoint: String) {
    actix_web::rt::spawn(async move {
        let mut rr_idx = 0;
        loop {
            let qname = QUEUE_NAMES[rr_idx % 5];
            rr_idx += 1;

            let user_opt = {
                let mut queues = queues.lock().unwrap();
                let q = queues.get_mut(qname).unwrap();
                if let Some(user) = q.users.front() {
                    // Check rate limit
                    let now = Instant::now();
                    if let Some(last) = q.last_request_time {
                        if now.duration_since(last) < MIN_REQUEST_INTERVAL {
                            // Not enough time passed
                            None
                        } else {
                            Some(user.clone())
                        }
                    } else {
                        Some(user.clone())
                    }
                } else {
                    None
                }
            };

            if let Some(user) = user_opt {
                // Process request
                let queues2 = queues.clone();
                let db2 = db.clone();
                let endpoint2 = endpoint.clone();
                actix_web::rt::spawn(handle_user_request(user, queues2, db2, qname.to_string(), endpoint2));
            }

            actix_web::rt::time::sleep(MIN_REQUEST_INTERVAL).await;
        }
    });
}

// --- Request Handler ---
async fn handle_user_request(
    user: User,
    queues: SharedQueues,
    db: Arc<Mutex<Connection>>,
    queue_name: String,
    endpoint: String,
) {
    let mut attempt = 1;
    let params = user.parameters.clone();
    let request_data = serde_json::to_string(&params).unwrap_or_default();
    let user_id = {
        let conn = db.lock().unwrap();
        conn.query_row(
            "SELECT id FROM users WHERE websocket_id = ?1",
            params![user.websocket_id],
            |row| row.get(0),
        )
        .unwrap_or(0)
    };

    let mut success = false;
    let wait_times = [0, 10, 1800]; // 0s, 10s, 30min

    for wait in wait_times.iter() {
        if *wait > 0 {
            actix_web::rt::time::sleep(Duration::from_secs(*wait)).await;
        }
        let resp = reqwest::Client::new()
            .post(&endpoint)
            .json(&params)
            .send()
            .await;

        let (code, body) = match resp {
            Ok(r) => {
                let status = r.status().as_u16();
                let text = r.text().await.unwrap_or_default();
                (status, text)
            }
            Err(e) => (0, format!("Request error: {}", e)),
        };

        let last_response = format!("Code: {}, Body: {}", code, body);

        // Log attempt
        {
            let conn = db.lock().unwrap();
            let _ = conn.execute(
                "INSERT INTO request_logs (user_id, request_data, response_data, attempt) VALUES (?1, ?2, ?3, ?4)",
                params![user_id, request_data, last_response, attempt],
            );
        }

        if code == 200 {
            user.addr.do_send(WsMessage(format!("Success: {}", body)));
            success = true;
            break;
        }
        attempt += 1;
    }

    if !success {
        user.addr.do_send(WsMessage("All attempts failed. Sorry, please try again later.".to_string()));
    }

    // Remove user from queue and DB
    {
        let mut queues = queues.lock().unwrap();
        if let Some(q) = queues.get_mut(&queue_name) {
            q.users.pop_front();
            q.last_request_time = Some(Instant::now());
        }
    }
    {
        let conn = db.lock().unwrap();
        let _ = conn.execute(
            "DELETE FROM users WHERE websocket_id = ?1",
            params![user.websocket_id],
        );
    }
    broadcast_positions(&queues, &queue_name);
}

// --- WebSocket Handler Entrypoint ---
async fn ws_index(
    req: HttpRequest,
    stream: web::Payload,
    data: web::Data<AppState>,
) -> Result<HttpResponse, Error> {
    let id = Uuid::new_v4().to_string();
    let session = WsSession {
        id,
        queues: data.queues.clone(),
        db: data.db.clone(),
        queue_name: None,
        params: None,
    };
    ws::start(session, &req, stream)
}

// --- App State ---
struct AppState {
    queues: SharedQueues,
    db: Arc<Mutex<Connection>>,
}

// --- Main ---
#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let db = Arc::new(Mutex::new(setup_db()));
    let queues: SharedQueues = Arc::new(Mutex::new(
        QUEUE_NAMES
            .iter()
            .map(|&name| (name.to_string(), QueueState {
                users: VecDeque::new(),
                last_request_time: None,
            }))
            .collect(),
    ));

    // Read endpoint from file
    let endpoint = std::fs::read_to_string("REQUEST_ENDPOINT.txt")
        .expect("Failed to read REQUEST_ENDPOINT.txt")
        .trim()
        .to_string();

    start_request_processor(queues.clone(), db.clone(), endpoint.clone());

    println!("Server running at 127.0.0.1:8080");
    HttpServer::new(move || {
        App::new()
            .app_data(web::Data::new(AppState {
                queues: queues.clone(),
                db: db.clone(),
            }))
            .route("/ws/", web::get().to(ws_index))
    })
    .bind(("127.0.0.1", 8080))?
    .run()
    .await
}
