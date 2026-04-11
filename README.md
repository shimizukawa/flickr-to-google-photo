# flickr-to-google-photo

FlickrからGoogle Photosへ写真を移行するスクリプト / A script to migrate photos from Flickr to Google Photos.

## 機能 / Features

- Flickrのすべての写真（プライベート含む）のメタデータを取得
  - アルバム名・タイトル・説明・コメント・撮影日時・GPS情報・タグ
- 写真をFlickrから最高解像度でダウンロード
- ダウンロードした写真にEXIFメタデータ（GPS・撮影日時・タイトル）を書き込む
- Google Photosへアップロードし、アルバムに追加
- 移行状況・メタデータをローカルJSON（`data/photos/<id>.json`）に保存
- 移行済みの写真をFlickrから削除（オプション）
- 中断しても続きから再開可能（冪等性あり）

## セットアップ / Setup

### 必要なもの / Requirements

- Python 3.11 以上

### インストール / Install

```bash
pip install -e .
```

### 認証情報の設定 / Credentials

#### Flickr

1. [Flickr App Garden](https://www.flickr.com/services/apps/create/) でアプリを作成
2. API KeyとSecretを取得
3. 以下の`.env`ファイルを作成（または環境変数に設定）:

```
FLICKR_API_KEY=your_api_key
FLICKR_API_SECRET=your_api_secret
```

初回起動時にブラウザでFlickr認証が行われます。取得したトークンはflickrapiが自動的にキャッシュします（通常は`~/.flickr/`）。

環境変数でトークンを明示的に指定することもできます:

```
FLICKR_ACCESS_TOKEN=your_access_token
FLICKR_ACCESS_TOKEN_SECRET=your_access_token_secret
```

#### Google Photos

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. **Photos Library API** を有効化
3. OAuthクライアントID（デスクトップアプリ）を作成し、`client_secrets.json`としてダウンロード
4. `.env`に追記（デフォルトは`client_secrets.json`）:

```
GOOGLE_CLIENT_SECRETS_FILE=client_secrets.json
```

初回起動時にブラウザでGoogle認証が行われ、トークンが`data/google_token.json`に保存されます。

#### その他の設定

```
DATA_DIR=data   # メタデータ・ダウンロード・トークンの保存先（デフォルト: data）
```

## 使い方 / Usage

### メタデータの取得のみ

Flickrから全写真のメタデータをローカルに保存します（ダウンロード・アップロードは行いません）:

```bash
flickr-to-gphoto fetch-metadata
```

### 移行の実行

```bash
# 全写真を移行（Flickrからは削除しない）
flickr-to-gphoto migrate

# 全写真を移行し、Google Photosへのアップロード成功後にFlickrから削除
flickr-to-gphoto migrate --delete

# 特定の写真のみ移行
flickr-to-gphoto migrate --photo-id 12345678901
```

### 進捗確認

```bash
# サマリーの表示
flickr-to-gphoto status

# 写真一覧の表示
flickr-to-gphoto list-photos

# ステータスでフィルタ
flickr-to-gphoto list-photos --filter-status error
flickr-to-gphoto list-photos --filter-status pending
```

### デバッグログ

```bash
flickr-to-gphoto -v migrate
```

## データ構造 / Data Structure

```
data/
├── google_token.json          # Google OAuth token (自動生成)
├── downloads/                 # 一時ダウンロードディレクトリ
│   └── <filename>.<ext>
└── photos/                    # 写真ごとのメタデータ
    └── <flickr_id>.json
```

### メタデータJSON例

```json
{
  "flickr_id": "12345678901",
  "flickr_url": "https://www.flickr.com/photos/user/12345678901/",
  "title": "Sunset at the beach",
  "description": "Beautiful sunset",
  "date_taken": "2023-06-15 18:30:00",
  "tags": ["sunset", "beach", "nature"],
  "albums": ["Summer 2023"],
  "gps": {
    "latitude": 35.6812,
    "longitude": 139.7671,
    "altitude": 10.0
  },
  "comments": [
    {
      "author": "user1",
      "author_name": "Alice",
      "date_create": "1686820300",
      "content": "Great shot!"
    }
  ],
  "google_photo_id": "AbCdEfGhIjKlMn",
  "google_photo_url": "https://photos.google.com/photo/...",
  "status": "completed"
}
```

## 移行ステータス / Migration Status

| ステータス | 説明 |
|-----------|------|
| `pending` | 未処理 |
| `downloading` | Flickrからダウンロード中 |
| `downloaded` | ダウンロード完了 |
| `uploading` | Google Photosへアップロード中 |
| `uploaded` | アップロード完了 |
| `adding_to_album` | アルバムに追加中 |
| `completed` | 移行完了 |
| `deleting_from_flickr` | Flickrから削除中 |
| `deleted_from_flickr` | Flickrから削除完了 |
| `error` | エラー発生（`error_message`フィールドを参照）|

## 開発 / Development

```bash
pip install -e ".[dev]"
pytest
```
