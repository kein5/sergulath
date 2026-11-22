//! What the command line can ask of the driver, and the state it keeps (no SteamVR here).
//!
//! One command per UDP datagram on 127.0.0.1, one reply line back:
//!
//! ```text
//! hello                                  -> ok sergulath-driver <version>
//! connect <left|right>                   -> ok        (adds the controller the first time)
//! disconnect <left|right>                -> ok        (it stays added, reported as not connected)
//! pose <left|right> x y z qw qx qy qz    -> ok        (SteamVR's raw tracking space, metres)
//! button <left|right> <name> <0|1>       -> ok        (name: trigger, grip, menu, system)
//! state                                  -> ok {json}
//! ```

pub const PORT: u16 = 39581;
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Hand {
    Left,
    Right,
}

impl Hand {
    pub const ALL: [Hand; 2] = [Hand::Left, Hand::Right];

    pub fn index(self) -> usize {
        match self {
            Hand::Left => 0,
            Hand::Right => 1,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Hand::Left => "left",
            Hand::Right => "right",
        }
    }

    fn parse(text: &str) -> Result<Hand, String> {
        match text {
            "left" => Ok(Hand::Left),
            "right" => Ok(Hand::Right),
            other => Err(format!("unknown hand {other:?} (left or right)")),
        }
    }
}

/// The buttons each controller has, in the order of `HandState::buttons`.
pub const BUTTONS: [&str; 4] = ["trigger", "grip", "menu", "system"];

#[derive(Clone, Debug, PartialEq)]
pub enum Command {
    Hello,
    Connect(Hand),
    Disconnect(Hand),
    Pose(Hand, [f64; 3], [f64; 4]),
    Button(Hand, usize, bool),
    State,
}

pub fn parse(line: &str) -> Result<Command, String> {
    let words: Vec<&str> = line.split_whitespace().collect();
    let numbers = |items: &[&str]| -> Result<Vec<f64>, String> {
        items.iter().map(|w| w.parse::<f64>().map_err(|_| format!("not a number: {w:?}"))).collect()
    };
    match words.as_slice() {
        ["hello"] => Ok(Command::Hello),
        ["state"] => Ok(Command::State),
        ["connect", hand] => Ok(Command::Connect(Hand::parse(hand)?)),
        ["disconnect", hand] => Ok(Command::Disconnect(Hand::parse(hand)?)),
        ["pose", hand, rest @ ..] if rest.len() == 7 => {
            let n = numbers(rest)?;
            if n.iter().any(|v| !v.is_finite()) {
                return Err("pose values must be finite".into());
            }
            let q = [n[3], n[4], n[5], n[6]];
            let length = q.iter().map(|v| v * v).sum::<f64>().sqrt();
            if length < 1e-6 {
                return Err("the rotation quaternion has no length".into());
            }
            Ok(Command::Pose(Hand::parse(hand)?, [n[0], n[1], n[2]], q.map(|v| v / length)))
        }
        ["button", hand, name, value] => {
            let index = BUTTONS.iter().position(|b| b == name)
                .ok_or_else(|| format!("unknown button {name:?} ({})", BUTTONS.join(", ")))?;
            let down = match *value {
                "1" | "down" => true,
                "0" | "up" => false,
                other => return Err(format!("button state must be 0 or 1, not {other:?}")),
            };
            Ok(Command::Button(Hand::parse(hand)?, index, down))
        }
        [] => Err("empty command".into()),
        [name, ..] => Err(format!("unknown command or wrong arguments: {name:?}")),
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct HandState {
    /// Asked to be connected; the controller is added to SteamVR the first time.
    pub connected: bool,
    pub position: [f64; 3],
    /// w, x, y, z
    pub rotation: [f64; 4],
    pub buttons: [bool; 4],
    /// Bumped on every change, so the frame loop sends input only when something changed.
    pub revision: u64,
}

impl Default for HandState {
    fn default() -> Self {
        Self { connected: false, position: [0.0; 3], rotation: [1.0, 0.0, 0.0, 0.0], buttons: [false; 4], revision: 0 }
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct State {
    pub hands: [HandState; 2],
    /// Set by the driver side: SteamVR's object id once a controller was activated.
    pub object_ids: [Option<u32>; 2],
}

impl State {
    /// Applies a command and returns the reply line.
    pub fn apply(&mut self, command: Command) -> String {
        match command {
            Command::Hello => format!("ok sergulath-driver {VERSION}"),
            Command::State => format!("ok {}", self.to_json()),
            Command::Connect(hand) => self.change(hand, |h| h.connected = true),
            Command::Disconnect(hand) => self.change(hand, |h| {
                h.connected = false;
                h.buttons = [false; 4];
            }),
            Command::Pose(hand, position, rotation) => self.change(hand, |h| {
                h.position = position;
                h.rotation = rotation;
            }),
            Command::Button(hand, index, down) => self.change(hand, |h| h.buttons[index] = down),
        }
    }

    fn change(&mut self, hand: Hand, edit: impl FnOnce(&mut HandState)) -> String {
        let state = &mut self.hands[hand.index()];
        edit(state);
        state.revision += 1;
        "ok".into()
    }

    pub fn to_json(&self) -> String {
        let hands: Vec<String> = Hand::ALL.iter().map(|&hand| {
            let h = &self.hands[hand.index()];
            let buttons: Vec<String> = BUTTONS.iter().zip(h.buttons).map(|(n, b)| format!("\"{n}\":{b}")).collect();
            let id = self.object_ids[hand.index()].map_or("null".to_string(), |v| v.to_string());
            format!(
                "\"{}\":{{\"connected\":{},\"object_id\":{},\"position\":[{},{},{}],\"rotation\":[{},{},{},{}],\"buttons\":{{{}}}}}",
                hand.name(), h.connected, id, h.position[0], h.position[1], h.position[2],
                h.rotation[0], h.rotation[1], h.rotation[2], h.rotation[3], buttons.join(",")
            )
        }).collect();
        format!("{{\"version\":\"{VERSION}\",{}}}", hands.join(","))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn commands_parse_and_bad_ones_say_why() {
        assert_eq!(parse("connect right"), Ok(Command::Connect(Hand::Right)));
        assert_eq!(parse("button left trigger 1"), Ok(Command::Button(Hand::Left, 0, true)));
        assert_eq!(parse("button left grip up"), Ok(Command::Button(Hand::Left, 1, false)));
        let Ok(Command::Pose(Hand::Right, p, q)) = parse("pose right 0.1 1.2 -0.3 2 0 0 0") else { panic!() };
        assert_eq!(p, [0.1, 1.2, -0.3]);
        assert_eq!(q, [1.0, 0.0, 0.0, 0.0]);
        assert!(parse("pose right 0 0 0 0 0 0 0").unwrap_err().contains("no length"));
        assert!(parse("button left thumb 1").unwrap_err().contains("unknown button"));
        assert!(parse("connect middle").unwrap_err().contains("unknown hand"));
        assert!(parse("jump").is_err());
    }

    #[test]
    fn state_changes_bump_the_revision_and_disconnect_lets_go() {
        let mut state = State::default();
        assert_eq!(state.apply(Command::Connect(Hand::Left)), "ok");
        state.apply(Command::Button(Hand::Left, 0, true));
        assert_eq!(state.hands[0].revision, 2);
        state.apply(Command::Disconnect(Hand::Left));
        assert!(!state.hands[0].connected && !state.hands[0].buttons[0]);
        let json = state.apply(Command::State);
        assert!(json.starts_with("ok {\"version\""), "{json}");
        assert!(json.contains("\"left\":{\"connected\":false,\"object_id\":null"), "{json}");
        assert!(json.contains("\"trigger\":false"), "{json}");
    }
}
