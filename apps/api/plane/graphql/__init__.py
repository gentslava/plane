# OVERLAY: mobile-graphql — GraphQL gateway for the native Plane mobile app.
# The official app (com.plane.so, Flutter) loads all content via POST /graphql/.
# Plane Cloud serves this via a proprietary gateway that is absent from the
# open-source apiserver; this app reimplements it schema-first against our models.
# See overlay/features/mobile-graphql/ for scope, plan and the captured Cloud SDL.
