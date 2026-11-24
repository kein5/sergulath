# 参考リポジトリ2件と SERGULATH の詳細比較

調査日：2026-11-24（+09:00）
調査方法：2件の参考リポジトリを clone し、README だけでなくソースコードの実物を読んで SERGULATH と比較した（実機・PC 上での実行やビルドは行っていない）。

## 対象

| リポジトリ | ライセンス | 確認したコミット | 主な構成 |
|---|---|---|---|
| [JonahSimon/steam-frame-harness](https://github.com/JonahSimon/steam-frame-harness) | MIT | `ff54658`（2026-11-18） | Linux PC + Steam Frame（arm64 SteamOS）向けの Python CLI（`frame-ctl`）、C++ OpenVR ドライバー（`driver_framectl.so`）、CEF 注入スクリプト、ヘッドセット側常駐サービス |
| [keithbradner/driver_asvr](https://github.com/keithbradner/driver_asvr) | MIT | `486fbed`（2026-11-06） | Windows（D3D11）/ Linux（Vulkan）向けの C++17 OpenVR 仮想 HMD・コントローラー・トラッカードライバー（`driver_asvr`）、CLI（`asvr_ctl`）、GUI モニター（`asvr_monitor`） |
| SERGULATH（比較基準） | MIT | `develop` `7089f81`（2026-11-24）およびオープン中の PR #16（`feat/frame-shot`） | Windows 向け Python CLI（`sergulath`）、Rust 仮想コントローラードライバー（`driver_sergulath`）、SteamVR 同梱 `driver_null` による仮想 HMD、SSH 読み取り専用の `frame overlays` / `frame shot` |

どちらも公開から間もない個人プロジェクトであるため、以下の各節では **コードの実物から確かめられたこと** と **README やソースコメントの主張だけのこと** を分けて記す。

---

## 結論の早見表

| 対象 | 観点 | 分類・判断 | 概要 |
|---|---|---|---|
| `steam-frame-harness` | 1. 装着中・起床状態の維持（`/proximity`） | **原則とぶつかる** | Steam Frame 本体に C++ ドライバー、systemd 常駐ユニット、passwordless sudo、vrserver の vtable 書き換えを入れる設計。本体読み取り専用の原則と衝突する |
| `steam-frame-harness` | 2. ヘッドセット表示映像の取得 | **原則とぶつかる**（本体経路）／**見送り**（PC 経路） | 3経路すべて合成後パネル（カメラ画面等）は撮れない。PC 側 `spectacle` は `view save` と同等で Linux/KDE 専用。本体 CEF / Xwayland 経路は本体改変・常駐前提かつ用途が異なる |
| `steam-frame-harness` | 3. 実機と同じ身元の仮想 Frame コントローラー | **原則とぶつかる**（本体配置・`hideReal` フック）／**将来の補完候補**（PC 側プロパティ模倣のみ） | 本体側 `hs spawn` と vtable フックは原則と衝突。PC 側 `driver_sergulath` で OpenVR 正規 API の範囲だけ Frame のプロパティ／入力プロファイルを模倣する案は原則に反せず実施可能（作業見立て：小〜中、現時点では優先度低） |
| `driver_asvr` | 1. 仮想 HMD ドライバー（DirectMode・共有メモリ・CLI） | **取り入れる価値あり（補完／段階導入候補）** | Windows で全レイヤー（シーン＋オーバーレイ＋ダッシュボード）を合成し共有メモリへ公開。デスクトップウィンドウも較正プローブも不要で RDP/ヘッドレス耐性が高い。`null` の即時全面置換ではなく選択可能なバックエンド候補（作業見立て：中） |
| `driver_asvr` | 2. 姿勢・ボタンのプログラム制御 | **部分的に取り入れる価値あり** | 可動 HMD（`hmd_rotate` / `hmd_translate`）は視界追従・視線外れ・ルーム固定パネルの確認に効く（固定原点 `driver_null` の弱点を補う）。ボタン拡張は将来 ASTAROT がスティックや ABXY を使う場合に有効（作業見立て：小〜中） |
| `driver_asvr` | 3. 実機（`vrlink`）との優先順位・両立 | **条件付きで両立（設計上の制約あり）** | `vrlink` が先に HMD 枠を取るため、`forcedDriver="asvr"`＋SteamVR 再起動による排他切替が必要（今の `hmd on/off` と同じ）。HMD 拒否時はコントローラーも出さないため「実機 HMD＋仮想コントローラー」には使えず、`driver_sergulath` は別途残す必要がある |

---

## 1. `steam-frame-harness` との比較

SERGULATH の原則として **「Steam Frame 本体は読み取り専用とし、本体側を変更する場合は保守担当者の個別承認を得る」** がある。`steam-frame-harness` はヘッドセット本体側（Steam クライアントの CEF、systemd、OpenVR ドライバー登録、`vrserver` プロセス内部メモリ）に積極的に手を入れる設計であるため、この原則と衝突する箇所は採用可否の判定ではなく **「原則とぶつかる」** として分類する。

また、コード全体が Linux PC 前提（`mprotect`、`/proc/self/exe`、`xdotool`、`spectacle`、POSIX ソケット）で書かれており、Windows へそのまま持ち込むことはできない。

### 観点 1-1：ヘッドセットを「装着中・起きている」状態に保つ方法（`/proximity` の保持）

#### 相手のやり方

**コードの実物から確かめられたこと**
- `frame/install-driver.sh` が Steam Frame 本体（arm64）上で `driver/framectl.cpp` を `g++` でビルドして `driver_framectl.so` を作り、`/opt/steamvr/bin/linuxarm64/vrpathreg adddriver ~/framectl` で本体側の `~/.config/openvr/openvrpaths.vrpath` に登録する。
- `driver/framectl.cpp` は `vrserver` のメモリ空間内で `mprotect` を使い、`IVRDriverInput_005`（`CreateBooleanComponent` / `UpdateBooleanComponent` / `CreateScalarComponent` / `UpdateScalarComponent`）と `IVRServerDriverHost_006`（`TrackedDevicePoseUpdated`）の vtable スロットを実行時に書き換えてフックする。
- `frame-ctl worn on` は Frame 本体にフラグファイル `~/.local/state/framectl/worn` を作成し、本体側（および PC 側）の `driver_framectl` の UDP ポート `127.0.0.1:47823` へ `set hmd /proximity 1` を送る。フックされた `UpdateBooleanComponent` が本物ドライバーの `/proximity` 更新を上書きし、装着中（`true`）を偽装する。
- `frame/install.sh` はパスワードなし `sudo` を要求し、systemd ユーザーサービス `framectl-dashguard.service`（`frame/dashguard.py`）を本体に常駐させる。`dashguard.py` は 3 秒ごとにポーリングし、SteamVR 再起動後も `worn` / `dash-hide` フラグを再適用する。さらに `steam-web-debug-portforward.service` / `steamvr-web-debug-portforward.service` を `systemctl mask` する。
- ダッシュボード抑止（`nodash` / `dash close`）では、SteamVR ビルド番号 `1792185270` の逆アセンブルで見つけた非公開インターフェース `IVRServerInternal_XXX` のスロット 57/58 まで vtable パッチする実装になっている（他ビルドでは無効化）。

**README・コメントの主張だけのこと（当方で実測未確認）**
- README に「`worn on` で 5 秒以内に standby から active へ遷移し、7 分間維持できた（2026-11-17）」と記載がある。
- `frame-ctl` のコメントに、`worn` フラグを消し忘れるとヘッドセットが起き続けてバッテリーを消耗すること、`dash-hide` フラグの消し忘れで数日間ヘッドセットのダッシュボードが開けなくなるロックアウトが起きたこと（`2026-11-16 lockout`）が記されている。

#### SERGULATH の現状

- SERGULATH の `frame overlays` と PR #16 の `frame shot` は、Steam Frame 本体に一切のファイル配置・設定変更・常駐サービス導入を行わない。
- PR #16 の `frame shot`（`docs/checks/frame-shot.md`）では、ヘッドセットが起きている状態のパネル画像を得るには人が一時的に装着する必要がある、という制約が残っている。
- なお、2026-11-23 の SERGULATH 実測（机上に置いた Steam Frame、SteamVR 2.19.4）では、PC 側ドライバーから `ShouldBlockStandbyMode` を返してもヘッドセットは `idle` のままで、位置追跡が失われた状態では `ComputeOverlayIntersection` も VR View も有効にならなかった。

#### 差分

| 項目 | `steam-frame-harness` | SERGULATH（現状 + PR #16） |
|---|---|---|
| 起床・装着維持の手段 | 本体側 `vrserver` の `/proximity` を vtable パッチで `1` 固定＋常駐サービスで再適用 | なし（起床状態の撮影は人が装着、無人確認は PC 側仮想 HMD で代替） |
| Steam Frame 本体への変更 | ドライバー登録、バイナリ配置、systemd 常駐、サービス mask、CEF デバッグ有効化、passwordless sudo | なし（読み取り専用） |
| SteamVR 更新への耐性 | 低い（内部 vtable 書き換え・ビルド固有スロット依存） | 高い（公開 API / DRM ioctl 読み取りのみ） |
| 置き忘れた際のリスク | バッテリー消耗、ダッシュボードのロックアウト | なし（1回限りの読み取りコマンド） |

#### 取り入れる価値があるか

- **分類：原則とぶつかる**
- 「Steam Frame 本体は読み取り専用、本体側の変更は保守担当者の個別承認が要る」という原則と真正面から衝突する。本体へのドライバー導入・systemd 常駐・vtable パッチを伴うため、SERGULATH 側の判断で取り入れるべきではない。
- 技術的な観点でも注意がある。コードから確かなのは「`/proximity` のブール値を `1` に偽装する」ことまでであり、**机に置いたままの Steam Frame が物理的な移動なしに有効な 6DoF 追跡を取り戻し、カメラ画面や PC からのオーバーレイ合成が「人が装着した状態」と同等に撮れるか**はコードだけでは裏付けられない。実際、`frame-ctl` 側にも別途 HMD 姿勢の上書き機能（`pose hmd`）が存在する。したがって、原則の例外を認めたとしても `frame shot` の弱点を無条件に置き換えられるとは言い切れない。

---

### 観点 1-2：ヘッドセットの表示映像を取得する方法

#### 相手のやり方

**コードの実物から確かめられたこと**
`steam-frame-harness` のコードには映像・画面取得の経路が 3 つ実装されているが、**いずれも Steam Frame の合成後パネル（scanout）を直接読む仕組みではない**。

1. **`frame-ctl shot`（PC 側ミラー画面の撮影）**
   - `frame-ctl` が `xdotool` で PC 上の `"VR View"` ウィンドウを前面化・リサイズし、KDE の `spectacle -b -n -a` でウィンドウ撮影する。
2. **`tools/frame-ui-shot`（本体側 Steam CEF の 2D UI 撮影）**
   - SSH ポートフォワード経由で本体側 Steam のリモートデバッグポート `127.0.0.1:8080` に繋ぎ、Chrome DevTools Protocol（`Page.captureScreenshot`）で `SharedJSContext` 等の HTML/CEF ページを撮影する（SteamOS 開発者モードの CEF リモートデバッグ有効化が前提）。
3. **`frame-ctl native shot`（本体側フラットアプリのウィンドウ撮影）**
   - 本体の Xwayland 上で `xwininfo` → `xwd -id` → `ffmpeg` で単一アプリのウィンドウを取得する。Vulkan/D3D スワップチェーンで `xwd` が失敗（`BadMatch`）したときのフォールバックとして `gamescopectl screenshot` を呼ぶが、コード内のコメントにも「フォーカス中のアプリを撮るだけで指定アプリとは限らない」とある。

**README・コメントの主張だけのこと**
- README には「`frame-ctl shot` でヘッドセットに見えるものを撮れる」旨の記述があるが、コードの実態は PC の `"VR View"` ウィンドウ撮影であり、本体側コンポジターが描く独自画面（カメラ画面等）は写らない。

#### SERGULATH の現状

- PC 側：`sergulath view save`（VR View ウィンドウ）、`sergulath view save --headset`（仮想 HMD ウィンドウ＋PR #13 のオーバーレイ切り出し）。
- 本体側（PR #16 `sergulath frame shot`、`docs/checks/frame-shot.md`）：SSH 経由で一時的に `DRM_IOCTL_MODE_GETFB2` → dma-buf → EGL/GLES でスキャンアウトフレームバッファを直接読み出し、PNG 化する。
  - 実測記録（2026-11-23、`docs/checks/frame-shot.md`）：両目合成・レンズ歪み適用後の `4320x2160` を **約 0.83 秒** で取得。`XRService-CameraOverlay`（プレイエリア外カメラ画面）を含むパネル上の全表示が写る。前提は SSH 接続と 1 回限りの `sudo` パスワード入力のみで、本体にファイルや設定を残さない。

#### 差分

| 比較項目 | `steam-frame-harness`（3経路） | SERGULATH `frame shot`（PR #16） |
|---|---|---|
| 本体独自画面（`XRService-CameraOverlay` 等）の取得 | **不可**（`shot` は PC VR View のみ、`frame-ui-shot` は CEF 2D UI のみ、`native shot` はフラットアプリのみ） | **可**（DRM/KMS の最終 scanout を直接読むためパネル表示すべてが写る） |
| 取得の確実さ | PC `shot` はヘッドセット非追跡時に黒画。`native shot` は Vulkan で `xwd` が失敗し `gamescopectl` 任せ | パネルに scanout がある限り確実（画面消灯・スタンバイ時は空/対象なし） |
| 速さ | `spectacle` / CDP / `xwd+ffmpeg+scp`（コード上 `gamescopectl` 待機は 0.5 秒刻み最大 5 秒） | 実測約 0.83 秒（4320x2160 PNG 取得・転送込み、`docs/checks/frame-shot.md`） |
| 前提条件 | Linux PC（KDE `spectacle` + `xdotool`）、本体側 CEF リモートデバッグ有効化、常駐ツール導入 | Windows PC + SSH + 実行時 1 回の `sudo` パスワード入力のみ（本体無改変） |

#### 取り入れる価値があるか

- **本体側 CEF 撮影（`frame-ui-shot`）および本体側ツール配置：分類「原則とぶつかる」**（CEF リモートデバッグ有効化や本体ツール常駐を前提とするため。また ASTAROT 検証で必要な VR 合成画面・カメラ画面も撮れない）。
- **PC 側 `frame-ctl shot`：見送り**。理由：SERGULATH の `view save` と同じ PC VR View 経路であり、しかも Linux / KDE（`spectacle`、`xdotool`）専用で Windows 向け SERGULATH に利点がない。
- 結果として、ヘッドセット表示映像の取得については **SERGULATH の PR #16（DRM scanout 取得）の方が優位であり、置き換える理由はない**。

---

### 観点 1-3：本物と同じ身元の仮想 Frame コントローラー

#### 相手のやり方

**コードの実物から確かめられたこと**
- `driver/framectl.cpp` 内の `VController` が、Steam Frame 実機コントローラーと同じ OpenVR プロパティを設定してデバイスを登録する。
  - `Prop_TrackingSystemName_String = "cv"`
  - `Prop_ModelNumber_String = "Steam_Frame_Controller_Left"` / `"Steam_Frame_Controller_Right"`
  - `Prop_RenderModelName_String = "{frame_controller}frame_controller_left"` / `"right"`
  - `Prop_ManufacturerName_String = "Valve"`
  - `Prop_ControllerType_String = "frame_controller"`
  - `Prop_InputProfilePath_String = "{frame_controller}/input/frame_controller_profile.json"`
- 入力コンポーネントは Frame 準拠の 35 個（トリガー、グリップ、サムスティック、A/B/X/Y、バンパー、システム、メニュー、各種タッチ・静電容量）とハプティクス、さらに SteamVR の GLB モデルから焼いた 31 ボーンの指スケルトン（`hand_poses.h`）を実装する。
- 実機コントローラーがスリープ状態で残っていると右手・左手ロールを奪い合うため、フックした `TrackedDevicePoseUpdated`（`hPose`）で `hideReal = true` にし、実機コントローラーを切断扱いへ書き換えて仮想コントローラーにロールを取らせる。
- 同じドライバーを PC 側と本体側（`frame-ctl hs spawn`）の両方で動かせる。また本体側 `frame/ovprobe/ovprobe.cpp` では、`ComputeOverlayIntersection` に数値ヤコビアン＋ニュートン法を組み合わせてオーバーレイ上の任意 UV を狙うレーザー姿勢を求め、さらに読み戻し（`ray`）と世界座標ズレ補正（`fix`）まで実装している。

**README・コメントの主張だけのこと**
- `frame-ctl` のコメントに、ゲーム実行中に仮想コントローラーを `despawn` すると Fallout 4 VR（Heisenberg mod）がクラッシュしたため実行中 `despawn` を禁止している、との記述がある。
- Steam Frame 向けゲームのアクションバインディングが実機と同じプロファイルでそのまま認識される、という効能は SteamVR の仕組み上もっともらしいが、Windows 上の ASTAROT で実際に検証した記録はない。

#### SERGULATH の現状

- `driver/src/lib.rs`（Rust 製 `driver_sergulath`）は C++ コンパイラ不要でビルドでき、確認時だけ登録して UDP（`127.0.0.1`）経由で左右の仮想コントローラーを出す。
- 独自プロファイル＋レガシーバインディング（トリガー、グリップ、アプリケーションメニュー）を提供しており、ASTAROT のランチャー押下（`GetControllerState`）は 2026-11-23 の検証で 12/12 回成功している。
- SteamVR 内部の vtable パッチや実機コントローラーの強制隠蔽は一切行わない。

#### 差分

| 項目 | `steam-frame-harness` `VController` | SERGULATH `driver_sergulath` |
|---|---|---|
| コントローラー身元 | Steam Frame 実機と同一（`frame_controller`、35入力＋31ボーン指スケルトン） | 独自仮想コントローラー（トリガー・グリップ・メニュー） |
| 実機との競合回避 | vtable パッチ（`hideReal`）で実機を切断偽装 | 確認時のみ登録（実機待機時はそのまま追加、仮想 HMD 時は実機なし） |
| 本体側での仮想コントローラー（`hs spawn`） | あり（本体 `vrserver` に導入） | なし（本体は読み取り専用） |
| プラットフォーム・保守性 | Linux 専用 C++・vtable フック依存 | Windows / Linux 両ビルド可の Rust・公開 Driver ABI のみ |

#### 取り入れる価値があるか

- **本体側 `hs spawn` および vtable フックによる `hideReal`：分類「原則とぶつかる」**（前者は本体改変、後者は `vrserver` 内部メモリ改変であり SERGULATH の方針・保守性と相容れない）。
- **PC 側 `driver_sergulath` で Frame のプロパティ／入力プロファイルを模倣する考え方：将来の補完候補（現時点では優先度低・作業見立て：小〜中）**
  - 理由：vtable パッチを使わず OpenVR の正規 `IVRProperties` / `IVRDriverInput` だけで `ControllerType = "frame_controller"` や追加ボタン（スティック、A/B/X/Y）を名乗ること自体は原則に反しない。ただし、今の ASTAROT はレガシー `GetControllerState` のトリガー・グリップ・メニューしか使っておらず、現行ケース（E2E 26 件）は `driver_sergulath` のままで通る。
  - 将来、ASTAROT（または他アプリ）が SteamVR Input の `frame_controller` 専用バインディングやスティック・ABXY に依存するケースを追加するときに、作業見立て **小〜中**（`driver/src/lib.rs` にプロパティ切替オプションと追加コンポーネントを追加、Rust 側テスト追加）で取り入れれば十分。
  - なお、`ovprobe.cpp` の「狙った後に `GetDeviceToAbsoluteTrackingPose` で実際のレーザー姿勢を読み戻して `ComputeOverlayIntersection` で命中検算する」設計は、SERGULATH の `overlay press --dry-run` と発想が近く、将来座標ズレが起きたときの自己検証の参考になる。

---

## 2. `driver_asvr` との比較

`driver_asvr` は MIT ライセンスの C++17 プロジェクトで、Windows（D3D11）と Linux（Vulkan）の両方に対応する完全な仮想 HMD＋コントローラー＋トラッカーの SteamVR ドライバーである。Steam Frame 本体には一切触れず、PC 側だけで完結する。

### 観点 2-1：仮想ヘッドセットドライバーとしての作り（DirectMode、共有メモリ、`asvr_ctl`）

#### 相手のやり方

**コードの実物から確かめられたこと**
- **仮想 HMD 本体（`driver_asvr/CVirtualHmd.cpp`）**
  - `ITrackedDeviceServerDriver` と `IVRDisplayComponent` を実装。`IsDisplayOnDesktop() == false`、`IsDisplayRealDisplay() == false`、左右対称 90° FOV（`[-1, 1]`）、レンズ歪みなし、IPD 63 mm。
  - `Prop_ContainsProximitySensor_Bool = true` を設定し `/proximity` を常時 `true` にするほか、`CServerDriver::ShouldBlockStandbyMode()` も `!m_hmdRefused` を返すことで、`vrserver` が仮想 HMD をスタンバイに落とさないようにしている。
  - 解像度（既定 `1024x768`）、FPS（既定 `60`）、姿勢は `steamvr.vrsettings` の `[driver_asvr]` から読み込み、終了時に姿勢を保存する（`DriverSettings.cpp`）。
- **DirectMode と共有メモリ公開（Windows：`driver_asvr/win/CDirectModeOutput.cpp` / `CFramePublisher.cpp`）**
  - `IVRDriverDirectModeComponent` を実装し、`PickAdapter` で Microsoft Basic Render Driver（`0x1414`）を除いた離散 GPU の LUID を `Prop_GraphicsAdapterLuid_Uint64` として `vrcompositor` に渡す。
  - D3D11 デバイスは初回 `CreateSwapTextureSet` まで遅延生成する（起動時の `gameoverlayrenderer64` 競合クラッシュ回避）。
  - `SubmitLayer` で最大 `g_maxLayers` 枚のレイヤーを受け取り、`Present` で **レイヤー 0（シーン）の上に後続レイヤー（オーバーレイ・ダッシュボード）をアルファ合成** して 1 枚の左右並列テクスチャへ描き、ステージングテクスチャ経由で `PostPresent` 時に CPU 読み出しし、二重バッファの Windows 名前付きファイルマッピング `Local\driver_asvr_frame_v2` に書き出す。
  - デスクトップ上にウィンドウを作らないため、**画面ロック中や RDP 越しでも `steamvr.allowDisplayLockedMode = true` と組み合わせて映像が止まらない** 設計になっている（`CVRSettings.cpp`）。
  - **Linux 版との重要な違い（`driver_asvr/linux/CDirectModeOutput.cpp`）**：Linux 版（Vulkan + `shm_open`）は `SubmitLayer` で `m_layersThisFrame == 0`（シーン層）しか保持せず、オーバーレイ層は明示的に無視する（`"only the scene layer is captured"`）。オーバーレイ検証に使えるのは **Windows 版だけ** である。
- **CLI 制御（`driver_asvr/CControlServer_win.cpp`、`asvr_ctl/main.cpp`、`asvr_ctl/Screenshot.cpp`）**
  - Windows では名前付きパイプで 1 行 1 コマンドの制御プロトコルを待ち受け、`asvr_ctl -` で標準入力からのバッチ実行もできる。
  - `asvr_ctl screenshot <out.png> [--both] [--wait-new-frame]` は GPU に触れず共有メモリを直接読み、`frameCounter` の前後一致（最大 6 回リトライ）で破綻のないフレームを確保してから、既定で左目（`--both` で両目）を PNG 保存する。
- **GUI モニター自動起動（`CServerDriver.cpp`、`Platform.cpp`）**
  - HMD 登録成功後に `LaunchMonitor()` が `driver_asvr.dll` と同じディレクトリまたは 3 階層上の `asvr_monitor.exe` を探して起動する。無効化設定は `DriverSettings.cpp` にないが、`Platform.cpp` の実装上 **`asvr_monitor.exe` を配置しなければログを出してそのままヘッドレス動作を続ける**。

**README・コメントの主張だけのこと（当方で実測未確認）**
- SteamVR 2.19.4 上で ASTAROT のオーバーレイ（`ASTAROT0`〜`ASTAROT3`）を合成したときの実際の画質・遅延・安定性。
- Windows 11 の RDP セッションやディスプレイ消灯状態での `asvr_ctl screenshot` の成否。
- 60 fps 連続 CPU リードバック時の GPU/CPU 負荷。

#### SERGULATH の現状

- `sergulath hmd on/off/run` が SteamVR 同梱の `driver_null`（`steamvr.forcedDriver="null"`, `driver_null.enable=true`, `1920x1080` ウィンドウ）を有効化し、終わったらスナップショットから復元する。
- 映像取得は `sergulath view save --headset` が Win32 で `"Headset Window"` ウィンドウを撮影する。
- 単一オーバーレイの切り出し（PR #13、`sergulath/core/headset_view.py`、`docs/checks/headset-view.md`）は、DPI スケーリングやウィンドウ余白のズレを吸収するため、撮影のたびに一時的なマゼンタ較正プローブ用オーバーレイを 2 箇所に出して左右の目の切り出し枠を逆算している。
- 利点として追加の C++ ビルドや外部バイナリが不要で、SteamVR 標準機能だけで完結している。一方、デスクトップウィンドウ前提のため、画面ロック・RDP・ウィンドウ被り・DPI 差の影響を受けやすい弱点がある。

#### 差分

| 比較項目 | `driver_asvr`（Windows 版） | SERGULATH 現状（`driver_null` + `headset_view.py`） |
|---|---|---|
| 映像の取り出し方 | `IVRDriverDirectModeComponent` → 共有メモリ（`Local\driver_asvr_frame_v2`） | デスクトップ上の `"Headset Window"` を Win32 キャプチャ |
| オーバーレイ合成 | Windows 版は全レイヤー（シーン＋オーバーレイ＋ダッシュボード）を合成（Linux 版はシーンのみ） | コンポジターがウィンドウへ描いた全レイヤーを撮影 |
| 較正プローブ・DPI 対策 | 不要（解像度・左右半分・90° 対称 FOV が既知の厳密値） | マゼンタ較正プローブ（PR #13）でウィンドウ内の目領域を実測 |
| RDP・画面ロック・完全ヘッドレス | `allowDisplayLockedMode=true` で動作する設計（未実測） | ウィンドウ描画に依存するため弱い |
| 導入・保守コスト | C++17 / MSVC / Windows SDK / glm / nlohmann_json のビルド・配布が必要 | SteamVR 同梱 `driver_null` を使うため追加バイナリ不要 |
| 頭の姿勢 | `asvr_ctl` で移動・回転可能 | 立ち位置の原点に固定 |

#### 取り入れる価値があるか

- **判断：今すぐ土台を全面置換するのではなく、「補完・段階的な選択肢」として取り入れる価値がある。**
- 理由：
  - 現行の `driver_null` 経路はビルド不要・依存ゼロで既に E2E 26 件のうち仮想 HMD を使うケースが動いている。これをいきなり捨てて C++ プロジェクトのビルド／配布を必須にすると、開発・CI・利用者セットアップの負担が一気に増える。
  - 一方で、`driver_asvr` の Windows DirectMode＋共有メモリ公開は、SERGULATH が `headset_view.py` で苦労して回避している問題（デスクトップウィンドウ必須、DPI・枠ズレ較正プローブ、RDP/ロック画面での黒画、頭の位置固定）を構造レベルで解消する。特に完全無人・RDP・CI ランナーでの視覚確認を広げたいときに非常に効く。
- **入れる場合の進め方と作業の大きさの見立て**
  1. **案 A（推奨）：外部バックエンドとして選択式で統合（`sergulath hmd --backend asvr` 等）— 作業見立て：中**
     - `driver_asvr.dll` と `asvr_ctl.exe`（`asvr_monitor.exe` は置かない）を任意導入またはビルド補助コマンドで配置し、`sergulath hmd` のスナップショット／復元機構（`forcedDriver = "asvr"` + `allowDisplayLockedMode` 等）から制御する。
     - `view save --headset` が `asvr` 有効時は `asvr_ctl screenshot --wait-new-frame`（または Python からの共有メモリ直接読み）を使うように分岐させる。既定は `null` のまま残し、実測で安定を確認してから昇格を検討できる。
  2. **案 B（見送り推奨）：`IVRDriverDirectModeComponent` + D3D11 マルチレイヤー合成を Rust で自前実装 — 作業見立て：大**
     - 既存の `driver_sergulath` に D3D11 シェーダー合成・スワップテクスチャ管理・共有メモリ公開を足すと保守範囲が跳ね上がる。MIT ライセンスの `driver_asvr` をそのまま使うか最小限の調整で連携する方がはるかに合理的。

---

### 観点 2-2：姿勢とボタン入力のプログラム制御が SERGULATH のケースのどこに効くか

#### 相手のやり方

**コードの実物から確かめられたこと**
- `asvr_ctl`（`asvr_ctl/main.cpp`）と `CServerDriver::ExecuteControlCommand` / `CControllerDevice.cpp` が以下の制御を提供する：
  - HMD：`hmd_pose`（絶対姿勢）、`hmd_rotate <yaw> <pitch> [roll]`、`hmd_translate <x y z> [world|local]`
  - コントローラー：`ctrl <left|right> offset <pos+quat>`（頭相対）、`ctrl <left|right> aim <yaw> <pitch>`（視界中央基準のレーザー照準）、`ctrl <left|right> button <trigger|grip|system|a|b|x|y> <press|release|click>`、`ctrl <left|right> trigger <0..1>`、`ctrl <left|right> joystick <x> <y>`
  - トラッカー：`waist` など 11 部位の仮想トラッカーの姿勢と `tracking on|off`
  - 入力プロファイル：`{asvr}/input/asvr_controller_profile.json` が `legacy_binding`（`asvr_legacy_bindings.json`）と `remapping` を同梱している。
- 制約として、コントローラーの位置は `CControllerDevice::UpdateWorldTransform` で **常に HMD 姿勢からの相対オフセット**（`headPos + headRot * offsetPos`）として計算される。

**README・コメントの主張だけのこと**
- `asvr_legacy_bindings.json` 経由で ASTAROT の `GetControllerState`（トリガー、グリップ、メニュー）が意図どおり届くかは未実測（コード上 `application_menu` に相当する専用ボタンはなく、A/B/X/Y・system・grip・trigger 構成のため、メニューボタン要否の照合が必要）。

#### SERGULATH の現状

- `driver_sergulath` は左右コントローラーをワールド座標で直接配置でき、トリガー・グリップ・アプリケーションメニューを押せる。`sergulath overlay press` がオーバーレイの実測座標とサイズから狙いを計算してトリガーを押す。
- 一方、HMD 側は `driver_null` のため位置・向きが原点固定で、実行中に頭を振ったり歩いたりする操作ができない。

#### 差分

| 機能 | `driver_asvr` | SERGULATH 現状 |
|---|---|---|
| HMD の移動・回転 | 可（`hmd_pose` / `hmd_rotate` / `hmd_translate`） | 不可（`driver_null` は原点固定） |
| コントローラー座標系 | HMD 相対のみ（`offset` / `aim`） | ワールド座標指定（実測オーバーレイ位置へ直接向ける `overlay press` あり） |
| ボタン・軸の種類 | trigger（アナログ可）、grip、system、joystick、A/B/X/Y | trigger、grip、application menu（レガシー） |
| 仮想トラッカー | 11 部位＋追跡ロスト模擬（`tracking off`） | なし |

#### 取り入れる価値があるか

- **判断：HMD の姿勢制御は取り入れる価値あり（補完）。コントローラー制御は現状の `driver_sergulath` の置き換えにはしない。**
- **SERGULATH のケースに具体的に効く箇所**
  1. **頭追従・手首追従オーバーレイの視界検証（効果大）**：`ASTAROT0`（頭追従の1行字幕）が頭を回しても視界内に留まるか、`ASTAROT3`（左手首ランチャー）を手首と頭の角度を変えたときにフェードイン／フェードアウトするか、`ASTAROT2`（ルーム固定の VR パネル）に振り返って正対できるか、といったケースは固定原点の `driver_null` では確認できない。`hmd_rotate` / `hmd_translate` があれば無人 E2E の対象を広げられる。
  2. **VR パネル上の操作拡張（将来の効果中）**：将来 ASTAROT の VR パネルでスクロールや別ボタン操作を検証するとき、アナログトリガー・ジョイスティック・A/B/X/Y は役立つ。
  3. **見送り（置き換えない理由）**：ASTAROT 向けの既存 `overlay press` はワールド座標の計算と `driver_sergulath` に密結合しており、かつ `driver_sergulath` は後述のとおり実機 HMD と同居できる。コントローラー制御の主軸を `driver_asvr` へ全面移行する利点は薄く、仮に `driver_asvr` を仮想 HMD バックエンドとして使う場合でも、必要な範囲（HMD 姿勢移動・視界撮影）から連携するのが安全（作業見立て：小〜中）。

---

### 観点 2-3：実物ヘッドセット（`vrlink`）環境でのドライバー優先順位と、ASTAROT 実機確認との両立

#### 相手のやり方

**コードの実物から確かめられたこと**
- `CServerDriver::RegisterDevices()`（`driver_asvr/CServerDriver.cpp` L77–L109）は、`Init()` から **500 ms 待ってから** `RunFrame()` 内で `TrackedDeviceAdded(HMD)` を呼ぶ（`vrserver` 起動直後の `gameoverlayrenderer64` 競合クラッシュ回避、および `vrlink` 等の初期化待ちのため）。
- すでに別のドライバー（Valve Index の `lighthouse` や Steam Frame を繋ぐ Steam Link の `vrlink`）が HMD スロットを確保している場合、`TrackedDeviceAdded` は `false` を返す。
- その瞬間、`driver_asvr` は `m_hmdRefused = true` とし、**完全に撤退する**：
  - コントロールパイプサーバー（`m_controlServer`）を停止する
  - 仮想コントローラーも仮想トラッカーも **一切登録しない**
  - `asvr_monitor` を起動しない
  - `ShouldBlockStandbyMode()` も `false` を返す（実機のスタンバイを邪魔しない）
- `driver_asvr` の README にも明記されているとおり、`vrlink`（Steam Link）は実機がストリーミング中でなくても起動時に HMD スロットを先取りするため、`vrlink` が入った PC で `driver_asvr` を動かすには **`steamvr.forcedDriver = "asvr"` を設定して SteamVR を再起動する必要がある**。
- `asvr_monitor/CConfigCore.cpp` と `CVRSettings.cpp` による標準インストール（`Install()`）は、`openvrpaths.vrpath` に `asvr` を常時登録し、さらに `steamvr.vrsettings` の 6 項目（`activateMultipleDrivers`, `allowDisplayLockedMode`, `disableAsync`, `doNotFadeToGrid`, `turnOffScreensTimeout=500000`, `pauseCompositorOnStandby=false`）を一括変更して `.driver_asvr.bak` を1回だけ作る仕様になっている。

**README・コメントの主張だけのこと**
- `forcedDriver` を外して SteamVR を再起動すれば実機（`vrlink`）へ戻る点自体は、`CVRSettings::SetForcedDriver(false)` のコードと SERGULATH の `hmd off` の実績から整合しているが、Steam Frame + `vrlink` 環境での実往復は当方未実測。

#### SERGULATH の現状

- SERGULATH の「守ること 2・3」により、仮想ドライバーは確認中だけ登録し、変更する設定キーをすべてスナップショットして終了時（失敗時含む）に元へ戻す。
- `sergulath hmd on/off/run` は `steamvr.forcedDriver = "null"` を使って実機（`vrlink`）と排他切替しており、終了後に Steam Frame が自動で戻ることを 2026-11-23 に実測済み。
- 一方、`driver_sergulath`（コントローラー専用ドライバー）は HMD スロットを奪わないため、実機 `vrlink` が有効な状態でも横並びで読み込める。

#### 差分

| 観点 | `driver_asvr` | SERGULATH の運用モデル |
|---|---|---|
| `vrlink` との優先順位 | `vrlink` が先勝ち → `driver_asvr` は自動撤退。使うには `forcedDriver="asvr"`＋再起動 | `hmd on` が `forcedDriver="null"`＋再起動で排他切替 |
| 実機 HMD＋仮想コントローラーの同時利用 | **不可**（HMD 拒否時にコントローラーも出さない） | **可**（`driver_sergulath` は単独でロード可） |
| 普段の VR 利用への影響 | `asvr_monitor` の `Install()` を使うと常時登録＋`steamvr.vrsettings` 6 項目が恒久変更される | 確認中だけ登録、キー単位スナップショットで終了時復元 |

#### 取り入れる価値があるか

- **判断：ASTAROT の実機確認とは「時間分割（排他切替）」で両立できるが、運用方式には SERGULATH 側の制御を被せる必要がある。**
- 両立のための条件（見送り事項を含む）：
  1. **`asvr_monitor` の `Install()` / `Uninstall()` は使わない（見送り）**：理由：常時登録と `.driver_asvr.bak` による丸ごとバックアップは SERGULATH のキー単位スナップショット（守ること 2・3）と競合し、普段の Steam Frame 利用時にも `disableAsync` や `turnOffScreensTimeout=500000` が残ってしまう。導入するなら SERGULATH の `hmd` / `settings` 層から「確認中だけ登録・必要なキーだけスナップショット・終了時に復元」で管理すべき。
  2. **実機確認中は `driver_asvr` を無効（未登録）に戻す**：`forcedDriver="asvr"` を設定している間は Steam Frame が切り離されるため、実機確認と無人仮想確認は現行の `hmd run` と同じ排他モデルになる。
  3. **`driver_sergulath` は残す**：実機 Steam Frame を装着したまま仮想コントローラーだけ足したい場合、`driver_asvr` は HMD 拒否で全停止するため代わりにならない。

---

## 3. コード実物で確認できた事実と、README・コメント主張のみの切り分け

| リポジトリ | コード実物から確認できた事実 | README・コメントの主張のみ（当方未実測） |
|---|---|---|
| `steam-frame-harness` (`ff54658`) | ・Linux 専用 C++/Python 実装（`mprotect`, `xdotool`, `spectacle`）<br>・Frame 本体へ `driver_framectl.so` ビルド・登録、`framectl-dashguard` systemd ユニット配置、passwordless `sudo`、SteamOS サービス mask、CEF リモートデバッグ＋JS 注入を実施<br>・`IVRDriverInput_005` / `IVRServerDriverHost_006` とビルド `1792185270` 固有の `IVRServerInternal` を実行時 vtable パッチ<br>・画面取得 3 経路（`spectacle` / CEF `Page.captureScreenshot` / `xwd`・`gamescopectl`）のいずれもパネル scanout（カメラ画面等）は撮れない<br>・`VController` は Frame 実機と同一プロパティ・35入力・31ボーン指スケルトンを持ち、`hideReal` フックで実機を隠す | ・`worn on` で 5 秒以内に active 化し 7 分維持できた（2026-11-17）<br>・`worn` 放置によるバッテリー消耗、`dash-hide` 放置によるロックアウト（2026-11-16）<br>・ゲーム中 `despawn` による Fallout 4 VR クラッシュ<br>・`/proximity=1` のみで机上静止の Frame が PC 側 `ComputeOverlayIntersection` や `frame shot` で有用な起床・追跡状態になるか |
| `driver_asvr` (`486fbed`) | ・Windows（D3D11）で `IVRDriverDirectModeComponent` が全レイヤー（シーン＋オーバーレイ＋ダッシュボード）を合成し `Local\driver_asvr_frame_v2` へ二重バッファ公開<br>・Linux（Vulkan）はシーン層のみでオーバーレイ層を無視<br>・`CVirtualHmd` が `/proximity = true`＋`ShouldBlockStandbyMode` でスタンバイ抑止<br>・`asvr_ctl` が名前付きパイプで HMD/コントローラー/トラッカー制御、共有メモリから PNG 保存（`--wait-new-frame`、`--both`）<br>・`RegisterDevices()` は 500 ms 待機後、他 HMD（`vrlink` 等）がいれば全撤退（コントローラーも出さない）<br>・`asvr_monitor.exe` が無ければ GUI を出さずに動作する<br>・`asvr_monitor` の `Install()` は常時登録＋`steamvr.vrsettings` 6項目を変更 | ・SteamVR 2.19.4 と ASTAROT（`ASTAROT0`〜`ASTAROT3`）での実際の描画品質・安定性<br>・Windows 11 の RDP / 画面ロック中での `asvr_ctl screenshot` 動作<br>・60 fps CPU リードバック時の GPU/CPU 負荷<br>・`asvr_legacy_bindings.json` で ASTAROT の `GetControllerState` 全ボタンがそのまま動作するか |

---

## 4. 取り入れる候補・見送り・判断が要ること

### 取り入れる価値がある候補（実装は今回しない）

1. **`driver_asvr`（Windows 版）を `sergulath hmd` の選択式バックエンドとして評価・統合する（案 A）**
   - **理由**：デスクトップウィンドウ・較正プローブ（PR #13）不要で全レイヤー合成画像を共有メモリから取得でき、RDP・画面ロック耐性と可動 HMD（`hmd_rotate` / `hmd_translate`）を同時に得られる。`driver_null` を既定に残せば既存環境を壊さない。
   - **作業の大きさの見立て：中**（まずは手元 Windows 11 + SteamVR 2.19.4 + ASTAROT で `driver_asvr` 単体の疎通・ASTAROT オーバーレイ描画・`vrlink` 復帰を検証し、問題なければ `hmd --backend asvr`・スナップショット対象キー拡張・`view save --headset` 連携・`asvr_monitor.exe` 除外のヘッドレス配置を実装）。
2. **`driver_asvr` の可動 HMD 制御を使った新しい検証ケースの追加**
   - **理由**：頭追従字幕（`ASTAROT0`）や手首ランチャー（`ASTAROT3`）の角度依存フェード、ルーム固定パネル（`ASTAROT2`）への振り向き確認など、固定原点の `driver_null` ではカバーできない領域を無人化できる。
   - **作業の大きさの見立て：小〜中**（上記 1 の土台が入った前提で、`sergulath hmd pose/rotate/translate` ラッパーとケース定義を追加）。
3. **将来必要になった時点で、`driver_sergulath` に Frame 準拠プロパティ／追加ボタン（OpenVR 正規 API のみ）を任意追加**
   - **理由**：vtable パッチを使わず原則を守ったまま SteamVR Input バインディング互換を高められる。ただし現時点の ASTAROT はレガシー入力 3 種で足りているため、優先度は低い。
   - **作業の大きさの見立て：小〜中**。

### 原則とぶつかるもの（採用可否を勝手に決めず分類）

- `steam-frame-harness` の Steam Frame 本体側変更一式：
  - 本体側 `driver_framectl.so` ビルド・`openvrpaths.vrpath` 登録
  - `framectl-dashguard` systemd ユニット常駐と SteamOS サービスの `mask`
  - パスワードなし `sudo` の設定
  - CEF リモートデバッグ有効化と `SharedJSContext` への JS 注入（`frame-cef` / `frame-ui-shot`）
  - `vrserver` 内部 vtable の実行時パッチ（`/proximity` 偽装、`hideReal`、`nodash`）

### 見送るもの

- **`steam-frame-harness` の PC 側画面撮影（`frame-ctl shot`）**：SERGULATH `view save` と同等の PC VR View 撮影であり、Linux / KDE 専用のため不要。
- **`driver_asvr` の `asvr_monitor` インストーラー運用**：常時ドライバー登録と `steamvr.vrsettings` 6 項目の恒久変更が SERGULATH の「守ること 2・3」と衝突するため、SERGULATH 側のスナップショット／復元層で管理する。
- **`driver_asvr` による `driver_sergulath` の全面置換**：実機 HMD（`vrlink`）と仮想コントローラーを併用したい場面では `driver_asvr` が全撤退してしまうため、コントローラー専用ドライバーとしての `driver_sergulath` は維持する。
- **DirectMode D3D11 マルチレイヤー合成の Rust 自前再実装**：作業量が大きく（大）、MIT ライセンスの `driver_asvr` をそのまま評価・利用する方が保守コストに見合う。

### 判断が要ること

1. **Steam Frame 本体の読み取り専用原則を維持するか、`/proximity` 偽装の実験を例外承認するか**
   - 原則どおり本体無改変を維持するなら、`steam-frame-harness` 方式の起床維持は採用せず、「人が装着しての `frame shot`＋無人確認は PC 側仮想 HMD」という現行方針を続けることになる。
2. **`driver_asvr`（Windows 版）の疎通検証と選択式バックエンド導入を進めるか**
   - 進める場合、配布・導入形態を「利用者が別途ビルド／ダウンロードしてパス指定」にするか、「SERGULATH 側でビルド補助コマンドを用意する（MSVC 前提）」にするかの判断が要る。
3. **`driver_asvr` 導入時に `steamvr.vrsettings` の追加キー（`allowDisplayLockedMode`、`disableAsync`、`doNotFadeToGrid`、`turnOffScreensTimeout`、`pauseCompositorOnStandby`）を `sergulath hmd` のスナップショット対象に含めてよいか**
   - SERGULATH の守ること 2 に従い、キー単位でスナップショット・復元する前提で許可するかの確認が要る。
