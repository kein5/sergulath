//! `driver_sergulath`: a SteamVR driver with two virtual controllers (left and right hand).
//!
//! SERGULATH registers this driver only for a check and removes it afterwards. The controllers
//! are not added to SteamVR until the command line asks for them (`connect`), so a driver left
//! registered by accident adds nothing. The command line moves them and presses their buttons
//! over UDP on 127.0.0.1 (see `control`).
//!
//! The interfaces are C++ classes. This file builds their vtables by hand, in the order of
//! `openvr_driver.h` (OpenVR SDK 2.15.6), so the crate needs no C++ compiler. On Windows x64
//! (MSVC ABI) a member function that returns a struct takes the return slot after `this`;
//! elsewhere (Itanium ABI) before it. Only Windows is tried.

pub mod control;

use std::ffi::{c_char, c_void, CStr};
use std::net::UdpSocket;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, MutexGuard, OnceLock};
use std::thread::JoinHandle;
use std::time::Duration;

use control::{Hand, State};

// ---- OpenVR values --------------------------------------------------------------------------

const PROVIDER_VERSION: &CStr = c"IServerTrackedDeviceProvider_004";
const DEVICE_DRIVER_VERSION: &CStr = c"ITrackedDeviceServerDriver_005";
const HOST_VERSION: &CStr = c"IVRServerDriverHost_006";
const PROPERTIES_VERSION: &CStr = c"IVRProperties_001";
const INPUT_VERSION: &CStr = c"IVRDriverInput_003";
const LOG_VERSION: &CStr = c"IVRDriverLog_001";

const INIT_NONE: i32 = 0;
const INIT_INTERFACE_NOT_FOUND: i32 = 105;
const INIT_DRIVER_FAILED: i32 = 200;

const DEVICE_CLASS_CONTROLLER: i32 = 2;
const TRACKING_RUNNING_OK: i32 = 200;
const TRACKING_UNINITIALIZED: i32 = 1;

const TAG_INT32: u32 = 2;
const TAG_UINT64: u32 = 3;
const TAG_BOOL: u32 = 4;
const TAG_STRING: u32 = 5;
const PROP_TRACKING_SYSTEM_NAME: i32 = 1000;
const PROP_MODEL_NUMBER: i32 = 1001;
const PROP_SERIAL_NUMBER: i32 = 1002;
const PROP_WILL_DRIFT_IN_YAW: i32 = 1004;
const PROP_MANUFACTURER_NAME: i32 = 1005;
const PROP_INPUT_PROFILE_PATH: i32 = 1037;
const PROP_SUPPORTED_BUTTONS: i32 = 3001;
const PROP_AXIS1_TYPE: i32 = 3003;
const PROP_CONTROLLER_ROLE_HINT: i32 = 3007;
/// k_EButton_System, ApplicationMenu, Grip, SteamVR_Touchpad (Axis0) and SteamVR_Trigger (Axis1).
const LEGACY_BUTTONS: u64 = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 32) | (1 << 33);
const AXIS_TRIGGER: i32 = 3;
const PROP_CONTROLLER_TYPE: i32 = 7000;

const SCALAR_ABSOLUTE: i32 = 0;
const SCALAR_NORMALIZED_ONE_SIDED: i32 = 1;

#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq)]
struct Quaternion {
    w: f64,
    x: f64,
    y: f64,
    z: f64,
}

const IDENTITY: Quaternion = Quaternion { w: 1.0, x: 0.0, y: 0.0, z: 0.0 };

/// `vr::DriverPose_t`
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct DriverPose {
    pose_time_offset: f64,
    world_from_driver_rotation: Quaternion,
    world_from_driver_translation: [f64; 3],
    driver_from_head_rotation: Quaternion,
    driver_from_head_translation: [f64; 3],
    position: [f64; 3],
    velocity: [f64; 3],
    acceleration: [f64; 3],
    rotation: Quaternion,
    angular_velocity: [f64; 3],
    angular_acceleration: [f64; 3],
    result: i32,
    pose_is_valid: bool,
    will_drift_in_yaw: bool,
    should_apply_head_model: bool,
    device_is_connected: bool,
}

fn pose_of(hand: &control::HandState) -> DriverPose {
    let r = hand.rotation;
    DriverPose {
        pose_time_offset: 0.0,
        world_from_driver_rotation: IDENTITY,
        world_from_driver_translation: [0.0; 3],
        driver_from_head_rotation: IDENTITY,
        driver_from_head_translation: [0.0; 3],
        position: hand.position,
        velocity: [0.0; 3],
        acceleration: [0.0; 3],
        rotation: Quaternion { w: r[0], x: r[1], y: r[2], z: r[3] },
        angular_velocity: [0.0; 3],
        angular_acceleration: [0.0; 3],
        result: if hand.connected { TRACKING_RUNNING_OK } else { TRACKING_UNINITIALIZED },
        pose_is_valid: hand.connected,
        will_drift_in_yaw: false,
        should_apply_head_model: false,
        device_is_connected: hand.connected,
    }
}

/// `vr::PropertyWrite_t`
#[repr(C)]
struct PropertyWrite {
    prop: i32,
    write_type: i32,
    set_error: i32,
    buffer: *mut c_void,
    buffer_size: u32,
    tag: u32,
    error: i32,
}

// ---- calling SteamVR's interfaces -------------------------------------------------------------

/// The `index`th function of a C++ object's vtable.
unsafe fn method(object: usize, index: usize) -> usize {
    let vtable = *(object as *const *const usize);
    *vtable.add(index)
}

#[derive(Clone, Copy)]
struct Host {
    host: usize,
    properties: usize,
    input: usize,
    log: usize,
}

static HOST: Mutex<Option<Host>> = Mutex::new(None);

fn host() -> Option<Host> {
    *lock(&HOST)
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|e| e.into_inner())
}

fn log(message: &str) {
    let Some(host) = host() else { return };
    if host.log == 0 {
        return;
    }
    let text = format!("{}\n\0", message.replace('\0', " "));
    unsafe {
        let f: unsafe extern "C" fn(usize, *const c_char) = std::mem::transmute(method(host.log, 0));
        f(host.log, text.as_ptr() as *const c_char);
    }
}

unsafe fn generic_interface(context: usize, name: &CStr) -> usize {
    let f: unsafe extern "C" fn(usize, *const c_char, *mut i32) -> usize = std::mem::transmute(method(context, 0));
    let mut error = 0i32;
    let found = f(context, name.as_ptr(), &mut error);
    if error != 0 { 0 } else { found }
}

fn write_properties(container: u64, props: &mut [(i32, u32, Vec<u8>)]) {
    let Some(host) = host() else { return };
    let mut batch: Vec<PropertyWrite> = props.iter_mut().map(|(prop, tag, value)| PropertyWrite {
        prop: *prop,
        write_type: 0,
        set_error: 0,
        buffer: value.as_mut_ptr() as *mut c_void,
        buffer_size: value.len() as u32,
        tag: *tag,
        error: 0,
    }).collect();
    unsafe {
        let f: unsafe extern "C" fn(usize, u64, *mut PropertyWrite, u32) -> i32 = std::mem::transmute(method(host.properties, 1));
        let error = f(host.properties, container, batch.as_mut_ptr(), batch.len() as u32);
        if error != 0 {
            log(&format!("writing properties failed ({error})"));
        }
    }
}

fn string(value: &str) -> Vec<u8> {
    let mut bytes = value.as_bytes().to_vec();
    bytes.push(0);
    bytes
}

// ---- the controllers ---------------------------------------------------------------------------

/// Input components, in the order of `control::BUTTONS`, plus the trigger's value.
#[derive(Clone, Copy, Default)]
struct Runtime {
    added: bool,
    object_id: Option<u32>,
    clicks: [u64; 4],
    trigger_value: u64,
    sent_revision: Option<u64>,
}

static RUNTIME: Mutex<[Runtime; 2]> = Mutex::new([Runtime { added: false, object_id: None, clicks: [0; 4], trigger_value: 0, sent_revision: None }; 2]);
static STATE: OnceLock<Mutex<State>> = OnceLock::new();

fn state() -> &'static Mutex<State> {
    STATE.get_or_init(|| Mutex::new(State::default()))
}

#[repr(C)]
struct DeviceVtable {
    activate: unsafe extern "C" fn(*mut Device, u32) -> i32,
    deactivate: unsafe extern "C" fn(*mut Device),
    enter_standby: unsafe extern "C" fn(*mut Device),
    get_component: unsafe extern "C" fn(*mut Device, *const c_char) -> *mut c_void,
    debug_request: unsafe extern "C" fn(*mut Device, *const c_char, *mut c_char, u32),
    #[cfg(windows)]
    get_pose: unsafe extern "C" fn(*mut Device, *mut DriverPose) -> *mut DriverPose,
    #[cfg(not(windows))]
    get_pose: unsafe extern "C" fn(*mut DriverPose, *mut Device) -> *mut DriverPose,
}

#[repr(C)]
struct Device {
    vtable: &'static DeviceVtable,
    hand: usize,
}

// SteamVR keeps pointers to these for the life of the process; they never move.
struct Shared<T>(std::cell::UnsafeCell<T>);
unsafe impl<T> Sync for Shared<T> {}

static DEVICE_VTABLE: DeviceVtable = DeviceVtable {
    activate: device_activate,
    deactivate: device_deactivate,
    enter_standby: device_enter_standby,
    get_component: device_get_component,
    debug_request: device_debug_request,
    get_pose: device_get_pose,
};
static DEVICES: [Shared<Device>; 2] = [
    Shared(std::cell::UnsafeCell::new(Device { vtable: &DEVICE_VTABLE, hand: 0 })),
    Shared(std::cell::UnsafeCell::new(Device { vtable: &DEVICE_VTABLE, hand: 1 })),
];

fn guard<T>(default: T, f: impl FnOnce() -> T) -> T {
    catch_unwind(AssertUnwindSafe(f)).unwrap_or(default)
}

fn serial(hand: Hand) -> &'static CStr {
    match hand {
        Hand::Left => c"SERGULATH-LEFT",
        Hand::Right => c"SERGULATH-RIGHT",
    }
}

unsafe extern "C" fn device_activate(this: *mut Device, object_id: u32) -> i32 {
    guard(INIT_DRIVER_FAILED, || {
        let hand = Hand::ALL[(*this).hand];
        let Some(host) = host() else { return INIT_DRIVER_FAILED };
        let to_container: unsafe extern "C" fn(usize, u32) -> u64 = std::mem::transmute(method(host.properties, 3));
        let container = to_container(host.properties, object_id);
        let role: i32 = if hand == Hand::Left { 1 } else { 2 };
        write_properties(container, &mut [
            (PROP_TRACKING_SYSTEM_NAME, TAG_STRING, string("sergulath")),
            (PROP_MODEL_NUMBER, TAG_STRING, string("SERGULATH virtual controller")),
            (PROP_SERIAL_NUMBER, TAG_STRING, string(serial(hand).to_str().unwrap_or("SERGULATH"))),
            (PROP_MANUFACTURER_NAME, TAG_STRING, string("SERGULATH")),
            (PROP_WILL_DRIFT_IN_YAW, TAG_BOOL, vec![0]),
            (PROP_CONTROLLER_ROLE_HINT, TAG_INT32, role.to_le_bytes().to_vec()),
            // Legacy apps (GetControllerState) see the trigger only when axis 1 says it is one.
            (PROP_SUPPORTED_BUTTONS, TAG_UINT64, LEGACY_BUTTONS.to_le_bytes().to_vec()),
            (PROP_AXIS1_TYPE, TAG_INT32, AXIS_TRIGGER.to_le_bytes().to_vec()),
            (PROP_INPUT_PROFILE_PATH, TAG_STRING, string("{sergulath}/input/sergulath_controller_profile.json")),
            (PROP_CONTROLLER_TYPE, TAG_STRING, string("sergulath_controller")),
        ]);
        let mut runtime = lock(&RUNTIME);
        let entry = &mut runtime[hand.index()];
        entry.object_id = Some(object_id);
        entry.sent_revision = None;
        if host.input != 0 {
            let create_bool: unsafe extern "C" fn(usize, u64, *const c_char, *mut u64) -> i32 = std::mem::transmute(method(host.input, 0));
            let create_scalar: unsafe extern "C" fn(usize, u64, *const c_char, *mut u64, i32, i32) -> i32 = std::mem::transmute(method(host.input, 2));
            let names = [c"/input/trigger/click", c"/input/grip/click", c"/input/application_menu/click", c"/input/system/click"];
            for (slot, name) in entry.clicks.iter_mut().zip(names) {
                let error = create_bool(host.input, container, name.as_ptr(), slot);
                if error != 0 {
                    log(&format!("{}: creating {} failed ({error})", hand.name(), name.to_string_lossy()));
                }
            }
            let error = create_scalar(host.input, container, c"/input/trigger/value".as_ptr(), &mut entry.trigger_value,
                                      SCALAR_ABSOLUTE, SCALAR_NORMALIZED_ONE_SIDED);
            if error != 0 {
                log(&format!("{}: creating /input/trigger/value failed ({error})", hand.name()));
            }
            log(&format!("{}: input handles {:?} value {}", hand.name(), entry.clicks, entry.trigger_value));
        }
        lock(state()).object_ids[hand.index()] = Some(object_id);
        log(&format!("{} controller active as device {object_id}", hand.name()));
        INIT_NONE
    })
}

unsafe extern "C" fn device_deactivate(this: *mut Device) {
    guard((), || {
        let hand = Hand::ALL[(*this).hand];
        lock(&RUNTIME)[hand.index()].object_id = None;
        lock(state()).object_ids[hand.index()] = None;
    })
}

unsafe extern "C" fn device_enter_standby(_this: *mut Device) {}

unsafe extern "C" fn device_get_component(_this: *mut Device, _name: *const c_char) -> *mut c_void {
    std::ptr::null_mut()
}

unsafe extern "C" fn device_debug_request(_this: *mut Device, _request: *const c_char, response: *mut c_char, size: u32) {
    if !response.is_null() && size > 0 {
        *response = 0;
    }
}

unsafe fn fill_pose(this: *mut Device, out: *mut DriverPose) -> *mut DriverPose {
    let pose = guard(None, || Some(pose_of(&lock(state()).hands[(*this).hand])));
    if let Some(pose) = pose {
        out.write(pose);
    }
    out
}

#[cfg(windows)]
unsafe extern "C" fn device_get_pose(this: *mut Device, out: *mut DriverPose) -> *mut DriverPose {
    fill_pose(this, out)
}

#[cfg(not(windows))]
unsafe extern "C" fn device_get_pose(out: *mut DriverPose, this: *mut Device) -> *mut DriverPose {
    fill_pose(this, out)
}

// ---- the provider ------------------------------------------------------------------------------

#[repr(C)]
struct ProviderVtable {
    init: unsafe extern "C" fn(*mut Provider, usize) -> i32,
    cleanup: unsafe extern "C" fn(*mut Provider),
    interface_versions: unsafe extern "C" fn(*mut Provider) -> *const *const c_char,
    run_frame: unsafe extern "C" fn(*mut Provider),
    should_block_standby: unsafe extern "C" fn(*mut Provider) -> bool,
    enter_standby: unsafe extern "C" fn(*mut Provider),
    leave_standby: unsafe extern "C" fn(*mut Provider),
}

#[repr(C)]
struct Provider {
    vtable: &'static ProviderVtable,
}

static PROVIDER_VTABLE: ProviderVtable = ProviderVtable {
    init: provider_init,
    cleanup: provider_cleanup,
    interface_versions: provider_interface_versions,
    run_frame: provider_run_frame,
    should_block_standby: provider_should_block_standby,
    enter_standby: provider_noop,
    leave_standby: provider_noop,
};
static PROVIDER: Shared<Provider> = Shared(std::cell::UnsafeCell::new(Provider { vtable: &PROVIDER_VTABLE }));

struct Versions([*const c_char; 3]);
unsafe impl Sync for Versions {}
static VERSIONS: Versions = Versions([DEVICE_DRIVER_VERSION.as_ptr(), PROVIDER_VERSION.as_ptr(), std::ptr::null()]);

static STOP: AtomicBool = AtomicBool::new(false);
static LISTENER: Mutex<Option<JoinHandle<()>>> = Mutex::new(None);

unsafe extern "C" fn provider_init(_this: *mut Provider, context: usize) -> i32 {
    guard(INIT_DRIVER_FAILED, || {
        let found = Host {
            host: generic_interface(context, HOST_VERSION),
            properties: generic_interface(context, PROPERTIES_VERSION),
            input: generic_interface(context, INPUT_VERSION),
            log: generic_interface(context, LOG_VERSION),
        };
        if found.host == 0 || found.properties == 0 {
            return INIT_INTERFACE_NOT_FOUND;
        }
        *lock(&HOST) = Some(found);
        if found.input == 0 {
            log("no IVRDriverInput_003: the controllers will have no buttons");
        }
        STOP.store(false, Ordering::SeqCst);
        match UdpSocket::bind(("127.0.0.1", control::PORT)) {
            Ok(socket) => {
                let _ = socket.set_read_timeout(Some(Duration::from_millis(200)));
                *lock(&LISTENER) = Some(std::thread::spawn(move || listen(socket)));
                log(&format!("listening on 127.0.0.1:{}", control::PORT));
            }
            Err(e) => log(&format!("cannot listen on 127.0.0.1:{}: {e}", control::PORT)),
        }
        INIT_NONE
    })
}

fn listen(socket: UdpSocket) {
    let mut buffer = [0u8; 1024];
    while !STOP.load(Ordering::SeqCst) {
        let Ok((size, from)) = socket.recv_from(&mut buffer) else { continue };
        let line = String::from_utf8_lossy(&buffer[..size]).trim().to_string();
        let reply = guard("error internal".to_string(), || match control::parse(&line) {
            Ok(command) => lock(state()).apply(command),
            Err(e) => format!("error {e}"),
        });
        let _ = socket.send_to(reply.as_bytes(), from);
    }
}

unsafe extern "C" fn provider_cleanup(_this: *mut Provider) {
    guard((), || {
        STOP.store(true, Ordering::SeqCst);
        if let Some(listener) = lock(&LISTENER).take() {
            let _ = listener.join();
        }
        *lock(&HOST) = None;
    })
}

unsafe extern "C" fn provider_interface_versions(_this: *mut Provider) -> *const *const c_char {
    VERSIONS.0.as_ptr()
}

unsafe extern "C" fn provider_run_frame(_this: *mut Provider) {
    guard((), || {
        let Some(host) = host() else { return };
        let snapshot = lock(state()).clone();
        let mut runtime = lock(&RUNTIME);
        for hand in Hand::ALL {
            let wanted = &snapshot.hands[hand.index()];
            let entry = &mut runtime[hand.index()];
            if wanted.connected && !entry.added {
                entry.added = true;
                let added: unsafe extern "C" fn(usize, *const c_char, i32, *mut c_void) -> bool = std::mem::transmute(method(host.host, 0));
                let device = DEVICES[hand.index()].0.get() as *mut c_void;
                // SteamVR may activate the device inside this call; let go of the lock first.
                drop(runtime);
                let ok = added(host.host, serial(hand).as_ptr(), DEVICE_CLASS_CONTROLLER, device);
                log(&format!("adding the {} controller: {}", hand.name(), if ok { "ok" } else { "refused" }));
                runtime = lock(&RUNTIME);
                continue;
            }
            let Some(object_id) = entry.object_id else { continue };
            let pose = pose_of(wanted);
            let updated: unsafe extern "C" fn(usize, u32, *const DriverPose, u32) = std::mem::transmute(method(host.host, 1));
            updated(host.host, object_id, &pose, std::mem::size_of::<DriverPose>() as u32);
            if host.input != 0 && entry.sent_revision != Some(wanted.revision) {
                let update_bool: unsafe extern "C" fn(usize, u64, bool, f64) -> i32 = std::mem::transmute(method(host.input, 1));
                let update_scalar: unsafe extern "C" fn(usize, u64, f32, f64) -> i32 = std::mem::transmute(method(host.input, 3));
                for (handle, down) in entry.clicks.iter().zip(wanted.buttons) {
                    if *handle != 0 {
                        update_bool(host.input, *handle, down, 0.0);
                    }
                }
                if entry.trigger_value != 0 {
                    update_scalar(host.input, entry.trigger_value, if wanted.buttons[0] { 1.0 } else { 0.0 }, 0.0);
                }
                entry.sent_revision = Some(wanted.revision);
            }
        }
    })
}

unsafe extern "C" fn provider_should_block_standby(_this: *mut Provider) -> bool {
    guard(false, || lock(state()).block_standby)
}

unsafe extern "C" fn provider_noop(_this: *mut Provider) {}

/// The entry point SteamVR looks up in every driver DLL.
///
/// # Safety
/// Called by SteamVR with a NUL-terminated interface name and a writable return code.
#[no_mangle]
pub unsafe extern "C" fn HmdDriverFactory(interface_name: *const c_char, return_code: *mut i32) -> *mut c_void {
    guard(std::ptr::null_mut(), || {
        if !interface_name.is_null() && CStr::from_ptr(interface_name) == PROVIDER_VERSION {
            return PROVIDER.0.get() as *mut c_void;
        }
        if !return_code.is_null() {
            *return_code = INIT_INTERFACE_NOT_FOUND;
        }
        std::ptr::null_mut()
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn struct_layouts_match_openvr_driver_h() {
        // DriverPose_t: 8 + 32 + 24 + 32 + 24 + 3*24 + 32 + 2*24 + 4 + 4 bools = 280 (8-aligned).
        assert_eq!(std::mem::size_of::<DriverPose>(), 280);
        assert_eq!(std::mem::size_of::<PropertyWrite>(), 40);
        assert_eq!(std::mem::size_of::<DeviceVtable>(), 6 * std::mem::size_of::<usize>());
        assert_eq!(std::mem::size_of::<ProviderVtable>(), 7 * std::mem::size_of::<usize>());
    }

    #[test]
    fn the_factory_hands_out_the_provider_only() {
        unsafe {
            let mut code = 0;
            assert!(!HmdDriverFactory(PROVIDER_VERSION.as_ptr(), &mut code).is_null());
            assert!(HmdDriverFactory(c"IVRWatchdogProvider_001".as_ptr(), &mut code).is_null());
            assert_eq!(code, INIT_INTERFACE_NOT_FOUND);
        }
    }

    #[test]
    fn a_disconnected_hand_reports_no_pose() {
        let pose = pose_of(&control::HandState::default());
        assert!(!pose.pose_is_valid && !pose.device_is_connected);
        let mut hand = control::HandState { connected: true, ..Default::default() };
        hand.position = [0.1, 1.0, -0.5];
        let pose = pose_of(&hand);
        assert!(pose.pose_is_valid && pose.result == TRACKING_RUNNING_OK);
        assert_eq!(pose.position, [0.1, 1.0, -0.5]);
    }
}
