# SERGULATH

**Sergulath's Emulation, Recording, Gauging & Unattended-Launch Automation Testing Harness**

SteamVR のオーバーレイを使うアプリを、Windows の PC から実機のヘッドセット（まずは Steam Frame）で動かして確かめるための道具です。SteamVR の状態と設定、VR表示、オーバーレイの画素、機器の追跡を扱い、のちに仮想の機器も扱います。

[English](../README.md)

> 状態：段階0です。`watch` と `overlay` を入れました。ほかの道具は段階を追って移します（[計画](#計画)）。

## 使い方

どちらのコマンドも読み取りだけをします。すでに動いている SteamVR（`vrserver` と `vrcompositor`）にバックグラウンドの OpenVR クライアントとしてつなぎ、SteamVR を起動することはありません。

```sh
pip install .            # Python 3.11 以降、Windows

# ヘッドセットとコントローラーを20分間、1分ごとに記録する（接続、位置の有無、追跡の状態、ヘッドセットの活動の状態）
sergulath watch --out records/watch.csv --minutes 20

# アプリのオーバーレイ（プロファイルに書いたもの）の有無、表示、透明度、幅を表示する。--out を付けると画素も PNG で保存する
sergulath overlay --profile astarot --out records/overlays
```

記録には部屋やデスクトップ、ファイルの名前が写ることがあります。`records/` は git の管理から外しています。

## 作る理由

VR の中でオーバーレイを確かめるには、ヘッドセットをかぶる人が要ります。机に置いた Steam Frame は位置を見失い、数分で待機に入り、コントローラーもいっしょに切れ、VR表示（ミラー）も黒くなります。SERGULATH はこうした確認を毎回同じ手順で行え、できるところは人がいなくても行えるようにする手順をまとめます。アプリごとのリポジトリに、その場かぎりのスクリプトとして散らばらないようにするためです。

最初に対象にするアプリは [ASTAROT](https://github.com/kein5/astarot) です。アプリに固有の情報（オーバーレイのキー、ログの場所、探すログの行）はプロファイルに分けます。そのため、ほかの OpenVR のオーバーレイのアプリにも使えます。

## 計画

| 部品 | 受け持つこと | 段階 |
|---|---|---|
| `watch` | ヘッドセットとコントローラーの接続、位置の有無、追跡の状態、活動の状態を一定の間隔で記録する | 0（済み） |
| `overlay` | アプリのオーバーレイを一覧にし、画素を保存する（`IVROverlay::GetOverlayImageData`） | 0（済み） |
| `logs` | `vrserver.txt`、`vrcompositor.txt`、アプリのログから1本の時系列を作る | 0 |
| `steamvr` | SteamVR の状態を示して起動する。`vrmonitor` が動いているかを確かめ、ヘッドセットの接続を待つ | 0 |
| `settings`（PROCULO） | 待機とスリープの設定を読み、写しを取ってから変える。写しから元に戻せる | 0 |
| `view` | VR表示を開き、画像を保存する | 1 |
| `chaperone` | プレイエリアを読む。chaperone の設定 API の作業用の写しで一時的なプレイエリアを反映し、終わったら戻す | 1 |
| `driver` | 人がいなくても入力できる仮想コントローラー（OpenVR のドライバー）。確認のあいだだけ登録する | 2（段階1で必要とわかったら） |
| `profiles/` | アプリと版ごとの情報。まず `astarot.toml` を置く | 0（済み） |

段階0ではスクリプトとしてすでにある道具を移し、段階1では実機のヘッドセットを人なしでどこまで確かめられるかを測ります。段階2はその結果を見て決めます。

## 守ること

1. 何もしなければ読み取りだけにする。何かを変えるコマンドは明示の指定を求める。
2. SteamVR の設定、プレイエリア、ドライバーの登録を変える前に写しを取り、終わるとき（失敗したときも）に元に戻す。
3. 仮想のドライバーは確認のあいだだけ登録する。利用者がふだん VR を使うときに出てこないようにする。
4. アプリ自身の設定には触れない。道具がアプリを起動するときは、設定の保存先を別にする。
5. モデルの取得とマイクの使用は、アプリ自身の画面で本人の同意を得てから行う。
6. 記録には使った PC、時刻（UTC との差を含む）、各ソフトの版を書く。ある PC での結果を、一般の保証として扱わない。

## 必要なもの

- SteamVR が入った Windows 10 または 11
- クライアントの道具には Python 3.11 以降と [pyopenvr](https://github.com/cmbruns/pyopenvr)
- 仮想のドライバー（段階2）には MSVC の C++ のツールチェーン。CI でビルドする

## ライセンス

MIT です（[LICENSE](../LICENSE)）。pyopenvr と OpenVR SDK はそれぞれのライセンス（BSD-3-Clause）のままです（[NOTICE.md](../NOTICE.md)）。

名前は ASTAROT の内部のコードネームと同じく、アスタロトに仕えるとされる悪魔から取りました（[kein5/astarot#689](https://github.com/kein5/astarot/issues/689)）。Sergulath は『真正奥義書（Grimorium Verum）』でネビロスの配下に挙げられています。名前はただの呼び名で、超自然的な意味はありません。
