<!-- OVERLAY: mobile-auth -->

# Plane Mobile App — Reverse-Engineering Notebook

Полный журнал реверс-инжиниринга официального приложения **Plane: Projects, Wiki, AI**
(`com.plane.so`, Flutter) — чтобы повторно поднять перехват/анализ за минуты, а не часы.
Цель работы: научить наш self-hosted форк принимать нативный логин приложения.

См. также: [`API-CONTRACT.md`](./API-CONTRACT.md) — снятый контракт эндпоинтов.

---

## 0. TL;DR — как снять трафик приложения заново

1. Собрать **пропатченный APK** (раздел 4): netsec доверяет user-CA + вшитый mitm-CA, PAIRIP заноплен.
2. Поставить на устройство/эмулятор по adb. Включить прокси на mitmproxy. mitmproxy с `allow_hosts='plane\.so'` (Google не ломать — он pinned).
3. Логин — **только email/magic-code**, НЕ Google (accounts.google.com pinned).
4. Готовый патч-рецепт и скрипты — в разделах 4-5.

---

## 1. Получение и распаковка APK

- Источник: APKPure / apkpure.com (`com.plane.so`). Формат **XAPK** = base.apk + сплиты
  (`config.arm64_v8a`, `config.en`, `config.xxhdpi`).
- Flutter-код — в `base.apk!/lib/arm64-v8a/libapp.so` (Dart AOT-снапшот) и `libflutter.so`.
- Нативные строки эндпоинтов/ключей: `strings -n 5 libapp.so | grep -E "/api|/auth|plane.so"`.
- Java/dex (PAIRIP, MainActivity, плагины): `jadx -d out --no-res base.apk` или `apktool d base.apk`.
- Манифест (AXML): `apktool d` декодирует; CallbackActivity flutter_web_auth_2 =
  `com.linusu.flutter_web_auth_2.CallbackActivity`, scheme `app.plane.so`.

## 2. PAIRIP (Google Play licensing) — защита и обход

Приложение обёрнуто в **PAIRIP**: `Application = com.pairip.application.Application`.

- `Application.attachBaseContext()` → `LicenseClient.checkLicense(ctx)`.
- `LicenseContentProvider.onCreate()` → `new LicenseClient(ctx).initializeLicenseCheck()`.
- Оба ведут в **`LicenseClient.initializeLicenseCheck()`**, который запрашивает лицензию у Play;
  при сайдлоаде (нет Play-лицензии / нет связи с `android.googleapis.com`) → `LicenseActivity`
  → `System.exit(0)`. **Приложение мгновенно закрывается.**
- ⚠️ Здесь НЕТ VMRunner / проверки подписи (проверено: `grep -ri "VMRunner|Signature|Integrity"` пуст),
  поэтому достаточно занопить один метод.

**Обход (2 способа):**

- **Статически (для standalone-патча):** в `smali/.../LicenseClient.smali` тело
  `initializeLicenseCheck()V` заменить на `.locals 0` + `return-void`.
- **В рантайме (frida, для рутованного эмулятора):** hook `LicenseClient.initializeLicenseCheck`
  (no-op + `licenseCheckState = FULL_CHECK_OK`), плюс заглушить `handleError/startErrorDialogActivity/scheduleAppShutdown`.
  Спавнить надо `frida -f` (до запуска кода — проверка в ContentProvider очень ранняя).

## 3. Перехват HTTPS (Flutter/Dart)

Ключевые факты:

- Dart `HttpClient` на Android доверяет **системному** хранилищу, НЕ user-CA (если у приложения
  нет своего `networkSecurityConfig`). У `com.plane.so` своего netsec НЕТ → по умолчанию только system.
- Поэтому Charles/mitmproxy с user-CA **не расшифрует** без одного из:
  - **root** → положить CA в `/system/etc/security/cacerts/<hash>.0` (см. ниже);
  - **патч APK** → добавить `networkSecurityConfig` с доверием `user` + вшитым CA (раздел 4).
- **Google-домены pinned** (`accounts.google.com`, `*.googleapis.com`) — их нельзя расшифровывать,
  иначе SPA логина крашится. Решение: `mitmdump --set allow_hosts='plane\.so'` (перехват только plane.so,
  остальное — туннель).
- Cloud-API мобильного приложения — на **`api.plane.so`** (веб-SPA — на `app.plane.so`).

**Системный CA на рутованном эмуляторе (Android ≤13):**

```bash
HASH=$(openssl x509 -inform PEM -subject_hash_old -in ~/.mitmproxy/mitmproxy-ca-cert.pem | head -1)
cp ~/.mitmproxy/mitmproxy-ca-cert.pem /tmp/$HASH.0
adb root; adb remount
adb push /tmp/$HASH.0 /system/etc/security/cacerts/$HASH.0
adb shell chmod 644 /system/etc/security/cacerts/$HASH.0
```

## 4. Пропатченный APK для mitm (рецепт)

Цель: APK, который (а) запускается сайдлоадом (PAIRIP обойдён) и (б) доверяет нашему mitm-CA
без установки чего-либо на устройство.

```bash
# 1. декомпиляция со smali
apktool d -f -o patch base.apk
# 2. no-op PAIRIP
python3 -c "import re;f='patch/smali/com/pairip/licensecheck/LicenseClient.smali';\
s=open(f).read();new='.method public initializeLicenseCheck()V\n    .locals 0\n\n    return-void\n.end method';\
open(f,'w').write(re.sub(r'\.method public initializeLicenseCheck\(\)V.*?\.end method',new,s,1,re.DOTALL))"
# 3. вшить mitm CA + netsec
mkdir -p patch/res/raw patch/res/xml
cp ~/.mitmproxy/mitmproxy-ca-cert.pem patch/res/raw/mitmca.pem
cat > patch/res/xml/network_security_config.xml <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<network-security-config><base-config cleartextTrafficPermitted="true"><trust-anchors>
<certificates src="system"/><certificates src="user"/><certificates src="@raw/mitmca"/>
</trust-anchors></base-config></network-security-config>
XML
sed -i '' 's|<application |<application android:networkSecurityConfig="@xml/network_security_config" |' patch/AndroidManifest.xml
# 4. сборка + подпись (build-tools/<ver>/{zipalign,apksigner})
apktool b -o unsigned.apk patch
keytool -genkeypair -keystore k.jks -alias a -keyalg RSA -validity 10000 -storepass p -dname "CN=Patch"
for A in unsigned config.arm64_v8a config.en config.xxhdpi; do
  zipalign -f -p 4 $A.apk out/$A.apk; apksigner sign --ks k.jks --ks-pass pass:p --ks-key-alias a out/$A.apk; done
# 5. установка (удалить оригинал из-за смены подписи)
adb uninstall com.plane.so; adb install-multiple -r out/*.apk
```

> ⚠️ Только Dart-трафик доверяет вшитому CA. Логин идёт в **Chrome Custom Tab** — Chrome'у CA нужен
> в user-хранилище устройства (Settings → Security → Install certificate → CA). HSTS на `app.plane.so`
> не даёт «продолжить» с непроверенным сертификатом — установка CA обязательна для веб-логина.

## 5. Подключение прокси

- Эмулятор (host-loopback): `adb shell settings put global http_proxy 10.0.2.2:8080`. Dart honor-ит
  глобальный прокси на эмуляторе; на части реальных устройств `dart:io` его игнорирует (тогда нужен
  iptables-redirect на рутованном устройстве, либо dio honor-ит — у Plane основной клиент honor-ит).
- Реальное устройство (одна сеть с Mac): `adb shell settings put global http_proxy <MAC_LAN_IP>:8080`,
  mitmproxy на `--listen-host 0.0.0.0`.
- mitmproxy: `mitmdump --listen-host 0.0.0.0 -p 8080 --set allow_hosts='plane\.so' --ssl-insecure -s addon.py`.

## 6. Снятие эталона логина без устройства (curl magic-link)

Самый чистый способ получить эталонные ответы — пройти magic-link **через curl** (нужен код с почты):

```bash
curl -sc cj -X POST https://api.plane.so/auth/mobile/email-check/ -d '{"email":"X"}' -H 'Content-Type: application/json'
CSRF=$(curl -sc cj -b cj https://api.plane.so/auth/get-csrf-token/ | jq -r .csrf_token)
curl -sc cj -b cj -X POST https://api.plane.so/auth/mobile/magic-generate/ -d '{"email":"X"}' -H 'Content-Type: application/json' -H "X-CSRFToken: $CSRF"
# >>> код приходит на почту <<<
# ВАЖНО: нужен заголовок Referer: https://app.plane.so/ — иначе 200 вместо 302
LOC=$(curl -si -c cj -b cj -X POST https://api.plane.so/auth/mobile/magic-sign-in/ \
  -H 'Content-Type: application/x-www-form-urlencoded' -H 'Referer: https://app.plane.so/' \
  --data-urlencode "csrfmiddlewaretoken=$CSRF" --data-urlencode "email=X" --data-urlencode "invitation_id=" --data-urlencode "code=CODE" \
  | grep -i '^location:')                       # -> {web}/m/auth/?token=<opaque64>
TOK=<opaque из LOC>
curl -s -X POST https://api.plane.so/auth/mobile/token-check/ -d "{\"token\":\"$TOK\"}" -H 'Content-Type: application/json'  # -> {access_token, refresh_token}
curl -s -X POST https://api.plane.so/auth/mobile/session-token/ -H "Authorization: Bearer <access>"                          # -> {session_name, session_id}
```

## 7. Что и почему чинили в self-hosted (overlay)

Наш `apps/api/plane/authentication/mobile/` минтил **свои** JWT и возвращал из `session-token`
полный объект `{access_token, refresh_token, user, profile...}`. Приложение же ждёт из `session-token`
**`{session_name, session_id}`** (ключ веб-сессии) и падает с «unable to get your info», если его нет.
Исправлено под контракт (раздел API-CONTRACT.md): `token-check`→`{access,refresh}` (SimpleJWT-claims с `jti`),
`session-token`→создаёт Django-сессию и отдаёт `{session_name, session_id}`.

## 8. Грабли (потеряли время — не повторять)

- **PAIRIP** валит сайдлоад/патч без обхода `initializeLicenseCheck`.
- **Dart игнорит user-CA** без своего netsec — нужен патч или root.
- **accounts.google.com / \*.googleapis.com pinned** — расшифровка ломает SPA логина → `allow_hosts='plane\.so'`.
- **HSTS** на `app.plane.so` не даёт обойти cert-warning в Chrome → CA ставить обязательно.
- **HTTP/2**: `--set http2=false` ломает app.plane.so (он по HTTP/2) — НЕ выключать.
- **Эмулятор**: Play-образ не рутуется (`adb root` → "production builds"); нужен **Google APIs** образ.
  Софт-GPU (`swiftshader`) ломает touch-ввод Flutter; `-gpu auto` падает (грей-скрин). DNS эмулятора
  бывает сломан → обход через `/system/etc/hosts` или `-dns-server 8.8.8.8`.
- **magic-sign-in** без `Referer` отдаёт 200 вместо 302 (нет токена).
- **frida 17** убрал встроенный Java-мост (`'Java' is not defined`) → ставить **frida 16.x**.

## 9. Инструментарий

mitmproxy 11, frida 16.7.x (+ frida-server android-arm64 той же версии), apktool 3, jadx,
Android build-tools (zipalign/apksigner), Android cmdline-tools (sdkmanager/avdmanager),
Google APIs arm64 system image (рутуемый эмулятор).
