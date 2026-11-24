# SERGULATH

**Sergulath's Emulation, Recording, Gauging & Unattended-Launch Automation Testing Harness**

SteamVR のオーバーレイを使うアプリを、Windows の PC から実機のヘッドセット（まずは Steam Frame）で動かして確かめるための道具です。SteamVR の状態と設定、VR表示、オーバーレイの画素、機器の追跡を扱い、のちに仮想の機器も扱います。

[English](../README.md)

> 状態：段階0です。`watch`、`overlay`、`profiles` を入れました。ほかのコマンドは段階を追って移します（[計画](#計画)）。

## コマンドラインを中心にする

SERGULATH はコマンドラインの道具です。できることはすべて `sergulath` のサブコマンドにし、手で打っても、スクリプトや CI、ほかのエージェントから呼んでも使えるようにします。コマンドどうしはパイプとファイルでつなげます。下にある Python のパッケージ（`sergulath.core`）は何も表示せず、コマンドから呼ぶための部品です。それだけで使う道具としては扱いません。

どのコマンドも次の決まりを守ります。

- データは標準出力に、メッセージは標準エラー出力に出す。
- `--format text`（既定）は人が読むための形。`--format json` は JSON Lines（1行に1件のオブジェクト）で出し、`jq` やほかのコマンドに渡せる。
- 入力を求めたり、入力を待ったりしない。
- 終了コード：`0` 完了、`1` 実行したが探したものがなかった、`2` 使い方の誤り、`3` SteamVR が動いていない（コマンドが SteamVR を起動することはない）、`4` 失敗。
- コマンドが書くファイルは、オプション（`--csv`、`--save`）で名前を指定したものだけ。

## 使い方

```sh
pip install .            # Python 3.11 以降、Windows

sergulath steamvr status                   # SteamVR が動いているか、状態ウィンドウ、版、ヘッドセットとコントローラーの状態
sergulath profiles                         # 同梱のアプリのプロファイルと、オーバーレイのキー

# ヘッドセットとコントローラーを1分ごとに20分間（21回）記録する（接続、位置の有無、追跡の状態、ヘッドセットの活動の状態）
sergulath watch --csv records/watch.csv

# 同じ記録に加えて、Steam Frame が自分の画面（「プレイエリア外」のカメラの画面など）を出しているかも SSH で読む
sergulath watch --frame

# アプリのオーバーレイの有無、表示、透明度、幅を表示する。--save を付けると画素も PNG で保存する
sergulath overlay --profile astarot --save records/overlays
# --placement：各オーバーレイが何に付いているか（頭、どちらの手、部屋）と、その結果どこにあるか
sergulath overlay --placement
# 手首のランチャーを時間で追う：装着中に手に付いて、見えるようになるか
sergulath watch --count 120 --interval 1 --overlay ASTAROT3

# 組み合わせの例：複数行の字幕のオーバーレイが、いま表示されているか
sergulath overlay --format json | jq -e 'select(.key == "ASTAROT1") | .visible'
```

### 人が PC の前にいなくても字幕を確かめる

アプリと SteamVR が動いていれば使えます。アプリがトレイにいても、手で何かを開く必要はありません。

```sh
sergulath caption sample start              # メイン画面を出し、字幕の設定のページを開き、アプリのサンプルを始める
sergulath caption check --view records/caption.png   # 一行の字幕のオーバーレイが表示され、描かれていれば終了コード0
sergulath caption sample start --line multi # 複数行の字幕で同じことをする（左手に付く）
sergulath caption check --line multi
sergulath caption sample stop               # サンプルを止め、設定のページを閉じる
```

仕組み：アプリの実行ファイルをもう一度起動すると、動いているアプリに引き継がれ、そのアプリがメイン画面を出します（アプリは1つのまま）。そのあと、Windows の UI Automation でボタンをアクセシビリティ上の名前で押します。名前はプロファイルに UI の言語ごとに書いてあります（ASTAROT は `[ui.ja]`）。サンプルはアプリ自身の機能で、設定のページを開いている間、字幕を1秒ごとに送ります。設定は変えません。`caption check` は字幕のオーバーレイが表示中で、透明でなく（字幕は最後の行から数秒で消えます）、描かれているかを読みます。VR表示に字幕が映るのは、ヘッドセットが位置を出している間だけです。机に置いた Steam Frame は位置を見失って VR表示が黒くなり、`--view` はそれを空の画像として記録します。

`watch`、`overlay`、`profiles`、`caption check` は読み取りだけをします。`caption sample` はアプリ自身のサンプルのボタンを押します。`watch` と `overlay` はすでに動いている SteamVR（`vrserver` と `vrcompositor`）にバックグラウンドの OpenVR クライアントとしてつなぎます。

記録には部屋やデスクトップ、ファイルの名前が写ることがあります。`records/` は git の管理から外しています。

## オーバーレイを押す

仮想コントローラーのドライバー（`driver/`。Rust で書いた SteamVR のドライバーの DLL）は、SteamVR に左手と右手のコントローラーを出します。SERGULATH は 127.0.0.1 の UDP でそれを動かし、ボタンを押します。登録するのは確認のあいだだけで（守ること 3）、`controller connect` を送るまでデバイスを足しません。

```sh
sergulath driver build --toolchain 1.100.0-x86_64-pc-windows-gnu   # 最初に1回。build/driver/sergulath に置く
sergulath driver register --restart       # SteamVR はドライバーを起動のときに読む。状態の窓から終了し、Steam から起動し直す
sergulath controller connect --hand left
sergulath controller pose --hand left --position -0.2 1.1 -0.4   # ランチャーは左手に付く
sergulath overlay press --overlay ASTAROT3 --button log --dry-run # 狙って、SteamVR 自身の当たり判定を表示する
sergulath overlay press --overlay ASTAROT3 --button log           # トリガーも押す
sergulath driver unregister --restart     # 普段の状態に戻す
# 1つのコマンドのあいだだけ登録する（失敗しても解除する）:
sergulath driver run --restart -- sergulath overlay press --overlay ASTAROT3 --button log
```

`GetControllerState` でボタンを読むアプリ（ASTAROT もそう）には、ドライバーのレガシーの割り当てを通して、トリガー（`k_EButton_SteamVR_Trigger`）、グリップ、アプリケーションメニューが届きます。システムボタンは SteamVR が自分で使います。

2026-11-23 に、机の上に置いてスタンバイになった Steam Frame と SteamVR 2.19.4 で確かめました。ドライバーは Frame のドライバーと並んで読み込まれ、2つのコントローラーには正しい位置と左右の役割が付き、ASTAROT のランチャーは仮想の左手に付きました。ただし SteamVR の `ComputeOverlayIntersection` はどのオーバーレイにも当たりなしを返し、SERGULATH が自分で作った不透明なオーバーレイでも、オーバーレイのアプリとして呼んでも結果は同じです。ASTAROT のレーザーも同じ呼び出しを使うので、この状態では何も押せません。同じ状態で VR View も黒くなります。ドライバーからスタンバイを止めても（`standby block`。`ShouldBlockStandbyMode`）変わらず、ヘッドセットはどちらでも `idle` でした。ASTAROT には仮想のグリップが届きましたが（ログは `overlay grab: hand 2 grip true hit none`）、レーザーはどこにも当たりませんでした。残る違いは、ヘッドセットの位置が取れないことでした。

仮想のヘッドセット（`sergulath hmd`）を使うと、誰もかぶらずにランチャーを押せます（2026-11-23、同じ PC）。

```sh
sergulath hmd run --with-driver -- python examples/astarot_launcher_e2e.py 6
```

`hmd run` は `steamvr.forcedDriver` と `driver_null.*` の写しを取ってから設定を変え、仮想コントローラーを登録し、SteamVR を Steam から起動し直します。null のヘッドセットは立ち位置の原点に固定した頭の位置を返します。スクリプトはまず SteamVR のダッシュボードを閉じます（ゲームがないと SteamVR が開き、開いているあいだアプリにボタンが届きません）。次に、ランチャーが頭のほうを向くように左手を置き、ランチャーのログのボタンを6回押します。2回の実行で12回とも VR パネルが開閉し、手で試した10回もすべて開閉しました。終わると `hmd run` は設定を戻し、ドライバーを外し、SteamVR を起動し直します。Steam Frame は自動でヘッドセットに戻りました。仮想のヘッドセットを使うあいだ、Frame は SteamVR のヘッドセットではなく、PC からの映像は映りません。押すときはトリガーを 0.3 秒押し続けます。ASTAROT は 0.15 秒の押下を取りこぼし、0.6 秒は長押しになります（ログのボタンでは、ログを呼び戻す動作になります）。

## 作る理由

VR の中でオーバーレイを確かめるには、ヘッドセットをかぶる人が要ります。机に置いた Steam Frame は位置を見失い、数分で待機に入り、コントローラーもいっしょに切れ、VR表示（ミラー）も黒くなります。SERGULATH はこうした確認を毎回同じ手順で行え、できるところは人がいなくても行えるようにする手順をまとめます。アプリごとのリポジトリに、その場かぎりのスクリプトとして散らばらないようにするためです。

最初に対象にするアプリは [ASTAROT](https://github.com/kein5/astarot) です。アプリに固有の情報（オーバーレイのキー、ログの場所、探すログの行）はプロファイルに分けます。そのため、ほかの OpenVR のオーバーレイのアプリにも使えます。

## 計画

それぞれを `sergulath` のサブコマンドにします。何かを変えるコマンドには読み取りだけの形も用意し、変えるときは動作を表す語を明示して指定します。

| コマンド | 受け持つこと | 段階 |
|---|---|---|
| `sergulath watch` | ヘッドセットとコントローラーの接続、位置の有無、追跡の状態、ヘッドセットの活動の状態を一定の間隔で記録する | 0（済み） |
| `sergulath overlay` | アプリのオーバーレイを一覧にし、画素を保存する（`IVROverlay::GetOverlayImageData`）。`--placement` で付いている先と位置を読み、`watch --overlay` で時間を追って記録する | 0（済み） |
| `sergulath profiles` | 同梱のアプリのプロファイル（アプリと版ごとの情報。まず ASTAROT のもの）を一覧にする | 0（済み） |
| `sergulath logs` | `vrserver.txt`、`vrcompositor.txt`、アプリのログから1本の時系列を作る（時刻は UTC との差を付けた現地時刻） | 0（済み） |
| `sergulath steamvr status` | SteamVR が動いているか、`vrmonitor` が動いているか、版、ヘッドセットとコントローラーの状態を示す | 0（済み） |
| `sergulath steamvr start` / `wait` / `stop` | SteamVR を Steam から起動し、ヘッドセットの接続（または追跡）を待ち、SteamVR を終了する | 0（済み） |
| `sergulath settings show` / `set` / `restore` / `pending` | SteamVR の設定（`section.name=値`）。変える前に対象のキーの写しを取り、キーごとに元に戻す | 0（済み） |
| `sergulath view save` / `screenshot` | ヘッドセットに映っているものを保存する。VR表示のウィンドウ（シーンとオーバーレイ）、仮想のヘッドセットのウィンドウ（`--headset`）、SteamVR のスクリーンショット（シーンだけ）のどれか。`--headset --overlay KEY` は、SteamVR が画素を渡さないオーバーレイを、仮想のヘッドセットの映像から切り出す。[PC から見えるもの](#pc-から見えるもの)と [docs/checks/headset-view.md](checks/headset-view.md) を参照 | 0（済み） |
| `sergulath caption sample start` / `stop`、`sergulath caption check` | アプリのサンプルの字幕をセッションの側から送り、字幕のオーバーレイが描かれているかを確かめる | 0（済み） |
| `sergulath frame overlays` | 単体で動くヘッドセット自身のシステムのオーバーレイを SSH で読む（状態と、読めるものは画像） | 1（試作） |
| `sergulath view open` / `close` | 人の手なしで VR表示を開き（`steamvr.showMirrorView` の写しを取って SteamVR を起動し直す）、元に戻す。[docs/checks/view-and-chaperone.md](view-and-chaperone.md) を参照 | 1（済み） |
| `sergulath chaperone show` / `apply` / `restore` | プレイエリアを読む。`chaperone_info.vrchap` の写しを取ってから、chaperone の設定 API で大きさ（と壁）を設定する。`restore` でファイルを書き戻す。SteamVR 2.19.4 は「一時的」な反映もファイルに書くので、変更は今の起動のあいだだけでは終わらず、`restore` まで残る | 1（済み） |
| `sergulath driver build` / `register` / `unregister` / `run` / `status` | 仮想コントローラーのドライバー（左手と右手を出す OpenVR のドライバー）。確認のあいだだけ登録する。[オーバーレイを押す](#オーバーレイを押す)を参照 | 2（試作。前倒し） |
| `sergulath controller connect` / `pose` / `button` / `dashboard` | 仮想コントローラーを動かし、ボタンを押す。`pose --show KEY` は、手首のオーバーレイがヘッドセットの正面に来るように手を置く。`dashboard close` は、アプリへのボタンを止める SteamVR のダッシュボードを閉じる | 2（試作） |
| `sergulath overlay press` | オーバーレイの実際の位置と大きさから、仮想コントローラーのレーザーを指定の場所に向けてトリガーを押す | 2（試作） |
| `sergulath hmd on` / `off` / `run` / `status` | 人がかぶらない確認のための仮想のヘッドセット（SteamVR に同梱の null ドライバー）。変える設定の写しを取り、失敗したときも元に戻す | 2（試作） |
| `sergulath asr serve` | speech.cpp の代わりのサーバー（モデルの一覧と Realtime の WebSocket）。決めた文を話者のラベル付きで送り、モデルも音もなしにアプリの字幕の経路を確かめられる。[docs/checks/speaker-names.md](docs/checks/speaker-names.md) を参照 | 2（試作） |
| `sergulath audio devices` / `check` | 音声デバイスを数秒聞き、音量（RMS とピーク、dBFS）としきい値を超えた長さを出す。再生デバイスはループバックで読む（マイクの許可は要らない）。録音デバイスは `--allow-input` のときだけ。[docs/checks/tts-virtual-mic.md](checks/tts-virtual-mic.md) を参照 | 2（済み） |
| `sergulath tts serve` | speech.cpp の代わりのサーバー。読み上げの依頼に音（または WAV）で答え、モデルなしでアプリの音声の経路を確かめられる | 2（済み） |
| `sergulath suite list` / `run` | ケース定義ファイル（TOML）から E2E のケースを順に実行し、成功、失敗、エラー、未対応、人手、スキップを数える。ASTAROT の 26 件は `suites/astarot-e2e.toml`。[docs/checks/e2e-suite.md](checks/e2e-suite.md) を参照 | 2（済み） |
| `sergulath soak` | 動いているアプリを一定時間（既定 30 分）見守り、プロセスが落ちないこと、ウォームアップ後のプロセスツリーのメモリ使用量が増え続けないこと、指定したオーバーレイが表示され続けることを確かめる（読み取りだけ）。[docs/checks/soak-and-upgrade.md](checks/soak-and-upgrade.md) を参照 | 2（済み） |
| `sergulath upgrade channel` / `check` | 更新チャネルのマニフェストを確認する（読み取りだけ）、または隔離したフォルダーで旧版（0.6.0）からベータチャネルかローカルの配布ファイルへ更新し、設定とモデルが引き継がれて通常起動できることを確かめる。[docs/checks/soak-and-upgrade.md](checks/soak-and-upgrade.md) を参照 | 2（済み） |
| `sergulath app status` / `start` / `stop` / `press` | 確認するアプリ。専用の設定フォルダーで起動し（`--settings-dir`、`--fresh`）、止め、ボタンを名前で押す | 2（済み） |

段階0ではスクリプトとしてすでにある道具をコマンドにし、段階1では実機のヘッドセットを人なしでどこまで確かめられるかを測ります。段階2はその結果を見て決めます。

## PC から見えるもの

2026-11-22 に、Steam Link でつないだ Steam Frame、SteamVR 2.19.4、VRChat で測りました。ヘッドセットの前に市松模様の試験用のオーバーレイを出して、どの経路に写るかを確かめています。

| 経路 | シーン（アプリの映像） | オーバーレイ（字幕、試験用のオーバーレイ） | ヘッドセット自身の画面（Steam Frame の「プレイエリア外」のカメラの画面） |
|---|---|---|---|
| VR表示のウィンドウ（`sergulath view save`） | 写る | 写る | 写らない。ヘッドセットがその画面を出している間、VR表示は黒くなった |
| SteamVR のスクリーンショットの API（`sergulath view screenshot`） | 写る | 写らない | 写らない |
| オーバーレイの画素（`sergulath overlay --save`） | 写らない | 1つずつ写る | 写らない |

単体で動くヘッドセットは、システムの画面をヘッドセットの側で描きます。PC のコンポジターを通らないので、PC 側のどの経路にも写りません。

ヘッドセットの上（Steam Frame に SSH で入り、登録した鍵を使う。`sergulath frame overlays`）：Frame は自分の SteamVR を動かしています（vrserver、vrcompositor、vrdashboard、追跡とカメラの XRService、PC の映像を受ける vrlink、Steam の 2D の画面の gamescope）。「プレイエリア外」の画面は、ヘッドセット上のオーバーレイ `system.systemui`（ボタン「プレイスペース外でカメラを有効にする」）と `XRService-CameraOverlay`（カメラの映像）です。ヘッドセット上のバックグラウンドのクライアントから見た結果は次のとおりです。

| ヘッドセット上の経路 | 結果 |
|---|---|
| オーバーレイの状態（表示、透明度） | どのシステムのオーバーレイでも読める。その画面が出ているかがわかる |
| オーバーレイの画素（`GetOverlayImageData`） | 画素で渡されたオーバーレイだけ読める。`system.systemui`（ボタンを含む UI の画像の寄せ集め）は読める。カメラの映像、ヘッドセットのビュー、Steam の画面は GPU のテクスチャで、エラー24 になる |
| ヘッドセットに映る合成後の画像 | まだ見つかっていない。コンポジターのデバッグ用のコマンド `screenshot_all_request` は受け付けられたが何も書かなかった。Vulkan の取得用のレイヤーを使うには、設定を変えてコンポジターを起動し直す必要があり、読み取りだけのこの道具ではしない |

VR表示にシーンが映るのは、ヘッドセットが位置を出している間だけです。机に置いた Frame が位置を見失う（追跡の状態が `calibrating_out_of_range`）と、ツールバーより下が一色（黒か灰色）になります。そのとき `view save` は画像が空だと報告し、終了コード1で終わります。仮想のヘッドセットでは VR表示はずっと黒いままです。null ドライバーがミラーを渡さないためで、そこでは `view save --headset` を使います。

## 守ること

1. 何もしなければ読み取りだけにする。変えるときは、動作を表す語かオプションを明示して指定する。
2. SteamVR の設定、プレイエリア、ドライバーの登録を変える前に写しを取り、終わるとき（失敗したときも）に元に戻す。
3. 仮想のドライバーは確認のあいだだけ登録する。利用者がふだん VR を使うときに出てこないようにする。
4. アプリ自身の設定には触れない。道具がアプリを起動するときは、設定の保存先を別にする。
5. モデルの取得とマイクの使用は、アプリ自身の画面で本人の同意を得てから行う。
6. 記録には使った PC、時刻（UTC との差を含む）、各ソフトの版を書く。ある PC での結果を、一般の保証として扱わない。

## 必要なもの

- SteamVR が入った Windows 10 または 11（CI は単体テストを Windows と Linux で動かす）
- クライアントの道具には Python 3.11 以降と [pyopenvr](https://github.com/cmbruns/pyopenvr)
- 仮想コントローラーのドライバーには Rust のツールチェーン（C++ のコンパイラーは要らない。ドライバーが自分で vtable を作る）。CI は Windows と Linux でビルドと試験をする。SteamVR で試したのは Windows だけ

## 変更の入れ方

ブランチの運用は ASTAROT と同じです。作業用のブランチで変更し、`develop`（既定のブランチ）へのプルリクエストにして、マージコミットでマージします。`main` はリリースのときだけ `develop` から更新します。コミットメッセージは Conventional Commits に従います。

## ライセンス

MIT です（[LICENSE](../LICENSE)）。pyopenvr と OpenVR SDK はそれぞれのライセンス（BSD-3-Clause）のままです（[NOTICE.md](../NOTICE.md)）。

名前は ASTAROT の内部のコードネームと同じく、アスタロトに仕えるとされる悪魔から取りました（[kein5/astarot#689](https://github.com/kein5/astarot/issues/689)）。Sergulath は『真正奥義書（Grimorium Verum）』でネビロスの配下に挙げられています。名前はただの呼び名で、超自然的な意味はありません。
