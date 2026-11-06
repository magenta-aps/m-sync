# Requirements

- mSync's goal is to synchronize data between incompatible groupware systems.
  - It synchronizes from one user in one system to the same user on another system.
  - It synchronizes only a small subset of event data:
    - Event start and end times, including full-day flags
    - Free/busy/tentative state
    - Event titles
  - No data should be synchronized across different users. The visibility of calendars
    across different users is entirely up to the external systems.
- Configuration of which users should be synced across what systems is done by looking up membership of a user in specific directory service groups.
  - Groups encode the pair of systems to be synced and the direction of the sync. So
    the same two systems may have up to two groups, one for each direction.
  - The same user cannot be a member of a system pair and its flipped counterpart. mSync should detect this and fail gracefully.
  - Opting out is done by removing a user from a system pair.
  - A user with the same identity needs to exist on both systems for mSync to work.
    - mSync should be able to provision the "ghost" user on the "mirror" side automatically
  - The system should be resilient to the "source" <-> "mirror" direction flipping, for any user at any moment.
- mSync continuously updates the "mirror" system to match the "source" system.
  - The state is eventually consistent. We give no hard guarantees that data is synchronized within any set timeframe.
  - Data from before the opt-in happened should also be synchronized.
  - Data is no longer synchronized after an opt-out, but data that was already synchronized is not removed on opt-out.

## Non-functional Requirements

- The majority of edits should appear in near-real-time, i.e. at most 5 seconds.
- Real-time updates are delivered on a best-effort basis, some loss of updates is accepted, as long as the correct state is eventually reached.
  - If no edits are being actively made, steady state should be reached within 24 hours.
- The system should be scalable to around ~10'000 users.
- The system should be able to recover from a crash and reach a steady state again.
- The following groupware systems should be supported
  - Cloud-based Nexcloud (CalDAV)
  - Microsoft Cloud (a.k.a. Microsoft 365, Microsoft Exchange Online, Microsoft Graph API)
- OIDC used for authentication against external systems.
- Directory service group membership through any of the following protocols/APIs:
    - Entra ID (Thorugh Microsoft Graph API)
- All systems agree on using the user "UID" attribute for uniquely identifying a user (different systems call it different things, on Microsoft Cloud it's called the `objectId`, Nextcloud calls it `uid`).
- Groups follow a pre-defined naming scheme, so mSync can discover them without configuration.
  - The format is `msync-<source>-<mirror>`
  - `<source>` and `<mirror>` can only contain alphanumeric characters and underscores
  - The entire string is case insensitive (e.g. `msycn-foo-bar` is the same as `mSync-Foo-Bar`).
