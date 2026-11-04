#!/bin/sh
# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

# Runs once, after Nextcloud is installed in compose and before it serves
# requests: the accounts and groups the integration tests use.
set -eu

OC_PASS="$MSYNC_SERVICE_PASSWORD" php /var/www/html/occ user:add --password-from-env --display-name=msync msync
OC_PASS="$MSYNC_MANAGER_PASSWORD" php /var/www/html/occ user:add --password-from-env --display-name=msync-manager msync-manager
php /var/www/html/occ group:add msync-ghosts
php /var/www/html/occ group:add msync-released

# The tests log in and create users faster than a person would.
php /var/www/html/occ config:system:set auth.bruteforce.protection.enabled --type=boolean --value=false
php /var/www/html/occ config:system:set ratelimit.protection.enabled --type=boolean --value=false
