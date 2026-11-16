#!/bin/sh
# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

# Runs once, after Nextcloud is installed in compose and before it serves
# requests: the accounts and groups the integration tests use.
set -eu

# Nextcloud copies 63 MB of demo files into every new account on its first login.
php /var/www/html/occ config:system:set skeletondirectory --value=''
php /var/www/html/occ config:system:set templatedirectory --value=''

# Nextcloud asks haveibeenpwned.com about every new password.
php /var/www/html/occ config:app:set password_policy enforceHaveIBeenPwned --type=boolean --value=false

# The tests use none of these. Each adds work for every new account.
php /var/www/html/occ app:disable circles
php /var/www/html/occ config:app:set firstrunwizard wizard_enabled --type=boolean --value=false
php /var/www/html/occ config:app:set dav create_example_event --type=boolean --value=false
php /var/www/html/occ config:app:set dav enableDefaultContact --type=boolean --value=false

OC_PASS="$MSYNC_SERVICE_PASSWORD" php /var/www/html/occ user:add --password-from-env --display-name=msync msync
OC_PASS="$MSYNC_MANAGER_PASSWORD" php /var/www/html/occ user:add --password-from-env --display-name=msync-manager msync-manager
php /var/www/html/occ group:add msync-ghosts
php /var/www/html/occ group:add msync-released

# The tests log in and create users faster than a person would.
php /var/www/html/occ config:system:set auth.bruteforce.protection.enabled --type=boolean --value=false
php /var/www/html/occ config:system:set ratelimit.protection.enabled --type=boolean --value=false
