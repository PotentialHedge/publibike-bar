# PubliBike menu bar

A tiny macOS menu bar app that shows live PubliBike / Velospot availability at your favourite stations.

- **Menu bar:** e-bikes ⚡ and bikes 🚲 at the favourite you're closest to (or one you pin).
- **Panel:** your favourites with live counts and distance, plus the nearest stations when you have fewer than three favourites.
- **Search as you type** to add stations (accent-insensitive, nearer stations first). Drag to reorder, hover or right-click to remove.
- Refreshes every minute in the background. About 1 MB, no Dock icon, no account or API key needed.

Requires macOS 14 (Sonoma) or newer. Universal binary (Apple Silicon and Intel).

## Install

```sh
brew tap potentialhedge/tap
brew trust --tap potentialhedge/tap   # Homebrew 7+ asks you to trust third-party taps once
brew install --cask potentialhedge/tap/publibike
```

Then open **PubliBike** from Applications. Allow location access when asked, so it can show the station nearest you. To start it automatically, open the panel, click ⚙︎ and choose **Launch at Login**.

The app isn't signed with an Apple Developer ID. The cask therefore removes the macOS download-quarantine flag after installing, so that Gatekeeper doesn't block it.

### Manual install

Download `PubliBike-<version>.zip` from [Releases](https://github.com/PotentialHedge/publibike-bar/releases), unzip it, and move `PubliBike.app` to Applications. The first time you open it, macOS will block it: go to **System Settings → Privacy & Security** and click **Open Anyway**.

## Build from source

```sh
cd PubliBikeBar
make install   # build for this Mac and copy to /Applications (replaces the Homebrew copy)
make dist      # universal zip in build/, prints its sha256
```

Needs Xcode 16 or newer (Swift 6 toolchain).

## Data

- **Live counts:** the public station list the official app uses (`velospot.info/customer/public/api/pbvsng/stations`). It's undocumented and unofficial, and may change without notice.
- **Station names and addresses:** the open [GBFS feed](https://mobilitydatabase.org/feeds/gbfs/gbfs-velospot_ch), cached for a day.

Favourites and settings stay on your Mac. Your location is used only on-device, to sort stations by distance.

This project isn't affiliated with PubliBike AG.

## Releasing

1. Bump `CFBundleShortVersionString` in `PubliBikeBar/Info.plist`.
2. Run `make dist`.
3. Create a GitHub release `vX.Y` with the zip attached.
4. Update `version` and `sha256` in `Casks/publibike.rb` in [homebrew-tap](https://github.com/PotentialHedge/homebrew-tap).

## Also included

`swiftbar/publibike.1m.py` is the original [SwiftBar](https://github.com/swiftbar/SwiftBar) plugin version (Python, no dependencies).
