# Синхронизация форка

Этот форк — самостоятельный **дистрибутив Plus** на базе `eyriehq/plane-plus`
(agent-first форк Plane с App Rail / Wiki / AI / epics / API-key поверх Plane 1.3.1),
с добавлением нашего кода и точечным переносом фич из других форков. Принцип изоляции
наших изменений — см. [`overlay/README.md`](overlay/README.md).

## Remotes
| remote | репозиторий | роль |
|--------|-------------|------|
| `origin` | `gentslava/plane` | наш форк — сюда коммитим и пушим |
| `eyriehq` | `eyriehq/plane-plus` | **база дистрибутива** — мержим `eyriehq/main` в `plus` |
| `upstream` | `makeplane/plane` | первоисточник Plane — наблюдаем (eyriehq сам мержит upstream к себе) |
| `shbvn` | `shbvn/plane` | наблюдаем, переносим time-tracking (старая база 0.19 — переписывать) |

## Ветки
- **`plus`** — ОСНОВНАЯ рабочая ветка и база дистрибутива. = `eyriehq/main` + наш overlay
  (CI, compose, docs) + наши фичи (time-tracking, PWA, mobile-auth). Из неё собираются
  образы `ghcr.io/gentslava/plane-*:plus` и разворачивается `myplane.gentslava.ru`.
- `master` — зеркало `upstream/master` (стабильные релизы Plane, напр. v1.3.1). Только для справки.
- `preview` — зеркало `upstream/preview` (dev-ветка Plane, впереди master). Только для наблюдения.
- ~~`develop`~~ — устаревшая ветка-эксперимент (чистый upstream baseline). Не используется,
  можно удалить.

> Почему база — eyriehq, а не upstream: фичи Plus (App Rail, Wiki-как-приложение, AI-ассистент,
> first-class epics) живут в **EE/коммерческом слое Plane** (`@/plane-web/`), который в upstream
> Community отключён (`apps/web/ce/.../app-rail/provider.tsx` → `isEnabled=false`). eyriehq переоткрыл
> их в своём AGPL-форке. Точечно портировать их на upstream — большой и бесконечный труд, поэтому
> берём eyriehq как базу. Цена — отставание от upstream/preview по «свежести» Plane (база 1.3.1).

---

## 1. Обновление базы из eyriehq
```bash
git fetch eyriehq
git checkout plus
git merge eyriehq/main          # подтянуть свежий Plus; наши правки — в overlay/, конфликты минимальны
git push origin plus            # push в plus автоматически пересобирает образы :plus (CI)
```
После сборки — redeploy `MyPlane` в Dokploy (тег `:plus` плавающий).

## 2. Наблюдение за upstream (makeplane)
```bash
git fetch upstream
git checkout master  && git merge --ff-only upstream/master  && git push origin master
git checkout preview && git merge --ff-only upstream/preview && git push origin preview
```
Прямой merge upstream в `plus` НЕ делаем (база — eyriehq). Свежие фиксы Plane приходят к нам
через eyriehq, когда они мержат upstream к себе. master/preview держим как зеркала для сравнения.

> `upstream` fetch ограничен `master` + `preview` (`git config remote.upstream.fetch`),
> чтобы не тянуть сотни feature-веток.

## 3. Перенос фичи из shbvn (или иных форков)
```bash
git fetch shbvn
```
1. Найти коммиты фичи: `git log shbvn/<branch> -- <путь>`.
2. Оценить изоляцию и объём.
3. Перенести:
   - **близко к нашей базе** → `git cherry-pick <sha>`, разрулить;
   - **далеко** (shbvn, 0.19) → **переписать** в `overlay/features/<name>/`, используя их код как референс.
4. Коммит в `plus` + smoke-тест.

## 4. Мониторинг развития форков
- **Дайджест (CI):** scheduled GitHub Action `.github/workflows/forks-digest.yml` — раз в день fetch'ит
  eyriehq/upstream/shbvn, собирает новые коммиты/релизы → открывает issue «Новое в форках».
- **Альтернатива (агент):** cron-задача в Hermes-агенте дёргает GitHub API трёх репозиториев и шлёт
  дайджест в Telegram. См. ROADMAP, Фаза 5.

## Частота
- `eyriehq` — раз в 1–2 недели или по их релизам (это наша база).
- `upstream` — обновлять зеркала по релизам, наблюдать.
- `shbvn` — точечно, при переносе time-tracking.
