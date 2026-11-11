# GitHub repositories are now mirror only

Due to recent changes in GitHub's direction, development has migrated to: https://git.ribas89.co.uk/

Have a issue/question? https://link.ribas89.co.uk/ask


Thank you for your support ❤️!


# Cursos

Cursos is a fork of [OfflineU](https://github.com/WhiskeyCoder/OfflineU) with user accounts, optional Authentik SSO, improved course browsing, and additional file previews.

## Features by OfflineU

- Load courses directly from folders without importing metadata
- Browse courses and lessons by folder structure
- Play video and audio lessons
- Read text, HTML, and PDF lessons
- Save lesson progress and resume playback
- Run locally without relying on a cloud service

## Additional features by Cursos

- Guest browsing with progress saved in the browser
- Local user accounts with SQLite progress storage
- Optional Authentik login using OpenID Connect
- Automatic SSO checks and linking to existing local accounts
- Custom Authentik avatar support using the `avatar` claim, including Base64 images
- Native previews for browser-supported images and other readable file formats
- Rendered Markdown previews with fenced code, tables, and disk caching
- Download any non-hidden regular course file, including ISO images and files without extensions
- Avatar refresh for linked SSO accounts
- Favorites, recently opened courses, and completion tracking
- Updated course library and lesson layouts
- English, Portuguese, and Spanish interfaces
- Light and dark themes
- Office document previews using LibreOffice
- ZIP, 7z, and RAR archive listings without extracting files
- Cached, scrollable PDF previews on mobile devices
- Optional random image card on the home page

## Docker Compose

Place `docker-compose.yml` next to the project's `Dockerfile`. Create a `.env` file and folders named `courses` and `data`.

```yaml
services:
  cursos:
    image: ghcr.io/ribas89/cursos:v2.0.0
    container_name: cursos
    restart: unless-stopped
    env_file:
      - .env
    ports:
      - "127.0.0.1:5000:5000"
    volumes:
      - ./courses:/app/courses:ro
      - ./data:/app/data
```

This example exposes Cursos only on the Docker host at `http://localhost:5000`. Configure a trusted reverse proxy if you need external HTTPS access. Keep the course mount read-only and make sure `/app/data` is writable by container user ID `1000`.

## Environment variables

| Variable | Purpose | Default or example |
| --- | --- | --- |
| `SECRET_KEY` | Flask session signing key. Required | A unique random secret |
| `COURSES_ROOT` | Location of the read-only course library inside the container | `/app/courses` |
| `DATABASE_PATH` | SQLite database for users and progress | `/app/data/cursos.sqlite3` |
| `COOKIE_SECURE` | Require HTTPS for session cookies. Set to `false` for local HTTP development only | `true` |
| `ALLOW_REGISTRATION` | Show or hide local account registration | `true` |
| `OFFICE_CONVERT_TIMEOUT` | Maximum time in seconds for an Office document conversion | `90` |
| `MEMES_ENABLED` | Enable the optional home page image card | `true` |
| `MEMES_DIR` | Image directory inside the container | `/app/memes` |
| `LIBREOFFICE_BINARY` | Optional path to the LibreOffice executable | System LibreOffice |

### Optional Authentik SSO

Create an OAuth2/OpenID Connect provider in Authentik and register `https://courses.example.com/auth/oidc/callback` as its redirect URI. Set all four required `OIDC_*` variables to enable SSO.

| Variable | Purpose | Example |
| --- | --- | --- |
| `OIDC_DISCOVERY_URL` | OIDC discovery document URL | `https://auth.example.com/application/o/cursos/.well-known/openid-configuration` |
| `OIDC_CLIENT_ID` | Client ID issued by Authentik | Your client ID |
| `OIDC_CLIENT_SECRET` | Client secret issued by Authentik | Your client secret |
| `OIDC_REDIRECT_URI` | Public callback URI registered with the provider | `https://courses.example.com/auth/oidc/callback` |
| `OIDC_REQUIRED_GROUP` | Optional required Authentik group claim | Leave unset or enter a group name |
| `OIDC_HOST` | Optional public Authentik hostname when discovery uses internal HTTP transport | `auth.example.com` |

Cursos requests the `openid`, `email`, and `profile` scopes. To use custom avatars, add a `profile` scope mapping that returns the user's custom `attributes.avatar` as the `avatar` claim. Steam avatar attributes are not used.

## License

MIT. Based on [OfflineU](https://github.com/WhiskeyCoder/OfflineU) by WhiskeyCoder.
