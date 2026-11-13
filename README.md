# mSync

mSync is a backend-only service that synchronizes calendars across different
incompatible groupware systems. Currently Microsoft Cloud (i.e. Microsoft Entra
for IdP and Microsoft Exchange Online for calendar data), and Nextcloud.

It is intended for large organizations that use a hybrid setup, with some users
on Microsoft products and some on open source alternatives. Since it is managed
centrally by the IdP service and administrators, users on the two ecosystems
can interact with each other's calendars as if they were on the same system,
without having to manually share calendars with each user.


## Installation and operation

All you need is some kind of container runtime, such as [podman](https://podman.io/) or
[docker](https://www.docker.com/).
Then you run `docker compose up -d --build`. This will read configuration from
the environment variables, and it will build the Dockerfile, which automatically
fetches all the dependencies it needs.

See `.env.example` for guidance on what configuration flags are supported.

> [!caution]
>
> It's a bad idea to write secrets in a plain `.env` file. You should set secrets
> dynamically in your deployment environment as environment variables, so your
> secrets only exist ephemerally in RAM, and not in some persistent file.


## Running tests

Unit tests do not require external dependencies, and can be run with a fake `.env` file.

To run integration tests, you need to have a `.env` file populated with the credentials
of whichever systems you have running in your test setup, such as Microsoft Cloud
and Nextcloud. See [.env.example](./.env.example) for a list of the available configuration
flags.

Then run:

```bash
# the test override adds a Nextcloud instance to the stack
docker compose -f compose.yaml -f compose.test.yaml up --build -d

# run all tests, you can also specify a path to run a subset of tests
docker compose -f compose.yaml -f compose.test.yaml run -it --rm sync pytest .
```

You also need the following to exist in external systems:
- Microsoft Entra
  - An app registration (that's the source of the client ID), should have the following
    permissions:
    - `Group.Read.All`
    - `User.Read.All`
    - `Calendars.ReadWrite.All`
  - A group called `msync-microsoft_cloud-nextcloud`, with users:
    - upn: `laurits-testensen@magentakommune.dk`
  - A group called `msync-nextcloud-microsoft_cloud`, with users:
    - upn: `anne-testensen@magentakommune.dk`

Some tests require a postgres instance to be running, for these, the easiest thing is to
run them inside a running docker compose stack. You can also spin up a local postgres instance
if you want to debug.


## Development

If you use [nix](nixos.org), you can spin up a development environment with all the dependencies
by calling `nix develop`.
