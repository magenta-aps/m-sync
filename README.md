# mSync

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
    - `Calendars.ReadBasic.All`
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
