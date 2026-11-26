# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

# The Nextcloud of compose.test.yaml with its post-installation hook baked in, for
# CI: GitLab services cannot mount files. Keep the base image in step with
# compose.test.yaml.
FROM nextcloud:34.0.4-apache

COPY compose/setup-nextcloud-test.sh /docker-entrypoint-hooks.d/post-installation/setup-nextcloud-test.sh
