import ServiceManagement
import SwiftUI

struct PanelView: View {
    @Environment(Store.self) private var store
    @State private var query = ""
    @State private var selection = 0
    @FocusState private var searchFocused: Bool

    private var results: [String] { store.search(query) }

    var body: some View {
        VStack(spacing: 0) {
            searchField
                .padding(10)
            Divider()
            Group {
                if !query.trimmingCharacters(in: .whitespaces).isEmpty {
                    suggestions
                } else {
                    home
                }
            }
            Divider()
            Footer()
        }
        .frame(width: 340)
        .onAppear {
            store.refreshIfStale()
            searchFocused = true
        }
    }

    // MARK: Search

    private var searchField: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField("Add a station…", text: $query)
                .textFieldStyle(.plain)
                .focused($searchFocused)
                .onChange(of: query) { selection = 0 }
                .onSubmit { addSelected() }
                .onKeyPress(.downArrow) { moveSelection(1); return .handled }
                .onKeyPress(.upArrow) { moveSelection(-1); return .handled }
                .onKeyPress(.escape) {
                    if query.isEmpty { return .ignored }
                    query = ""
                    return .handled
                }
            if !query.isEmpty {
                Button { query = "" } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.tertiary)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(.quaternary.opacity(0.6), in: RoundedRectangle(cornerRadius: 7))
    }

    private func moveSelection(_ delta: Int) {
        let n = results.count
        guard n > 0 else { return }
        selection = (selection + delta + n) % n
    }

    private func addSelected() {
        let r = results
        guard r.indices.contains(selection) else { return }
        add(r[selection])
    }

    private func add(_ id: String) {
        store.add(id)
        query = ""
        searchFocused = true
    }

    @ViewBuilder
    private var suggestions: some View {
        let r = results
        if r.isEmpty {
            Text(store.info.isEmpty ? "Loading stations…" : "No stations match “\(query)”")
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, minHeight: 60)
        } else {
            VStack(spacing: 2) {
                ForEach(Array(r.enumerated()), id: \.element) { i, id in
                    Button { add(id) } label: {
                        StationRow(id: id, trailingIcon: "plus.circle.fill")
                            .padding(.horizontal, 8)
                            .padding(.vertical, 5)
                            .background(i == selection ? Color.accentColor.opacity(0.18) : .clear,
                                        in: RoundedRectangle(cornerRadius: 6))
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .onHover { if $0 { selection = i } }
                }
            }
            .padding(6)
        }
    }

    // MARK: Home (empty search)

    /// Favourites first; then nearby stations: 5 with no favourites, 2 with fewer than 3.
    @ViewBuilder
    private var home: some View {
        let favCount = store.favorites.count
        let near = store.nearby(limit: favCount == 0 ? 5 : (favCount < 3 ? 2 : 0))
        VStack(spacing: 0) {
            if favCount > 0 { favoritesList }
            if !near.isEmpty {
                if favCount > 0 { Divider().padding(.horizontal, 10) }
                nearbySection(near)
            } else if favCount == 0 {
                emptyState
            }
        }
    }

    private func nearbySection(_ ids: [String]) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Label("Nearby", systemImage: "location.fill")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 8)
                .padding(.top, 4)
            ForEach(ids, id: \.self) { id in
                NearbyRow(id: id) { add(id) }
            }
        }
        .padding(6)
    }

    private var emptyState: some View {
        VStack(spacing: 6) {
            Image(systemName: "star").font(.title2).foregroundStyle(.tertiary)
            Text("No favourite stations yet").font(.headline)
            Text(store.locationDenied
                 ? "Type a station name above to add one,\nor allow location access to see nearby stations."
                 : "Type a station name above to add one.")
                .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 24)
    }

    private var favoritesList: some View {
        List {
            ForEach(store.favorites, id: \.self) { id in
                FavoriteRow(id: id, pinned: id == store.menuBarStation)
                    .listRowInsets(EdgeInsets(top: 4, leading: 6, bottom: 4, trailing: 6))
                    .listRowSeparator(.hidden)
            }
            .onMove { store.move(from: $0, to: $1) }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .frame(height: min(CGFloat(store.favorites.count) * 46 + 8, 420))
    }
}

// MARK: - Rows

struct StationRow: View {
    @Environment(Store.self) private var store
    let id: String
    var pinned = false
    var trailingIcon: String? = nil

    var body: some View {
        let info = store.info(for: id)
        let live = store.live[id]
        HStack(spacing: 8) {
            StatusDot(live: live)
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 4) {
                    Text(store.title(for: id)).fontWeight(.medium).lineLimit(1)
                    if pinned {
                        Image(systemName: store.showNearest && store.location != nil ? "location.fill" : "menubar.rectangle")
                            .font(.caption2).foregroundStyle(.secondary)
                            .help(store.showNearest && store.location != nil ? "Nearest – shown in the menu bar" : "Shown in the menu bar")
                    }
                }
                Text([store.formattedDistance(to: id), info?.place, info?.address]
                    .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 4)
            if let live {
                if live.outOfService == true {
                    Text("Closed").font(.caption).foregroundStyle(.secondary)
                } else {
                    Count(icon: "bolt.fill", value: live.eBikes, tint: .orange)
                    Count(icon: "bicycle", value: live.bikes, tint: .accentColor)
                }
            } else {
                Text("–").foregroundStyle(.secondary)
            }
            if let trailingIcon {
                Image(systemName: trailingIcon).foregroundStyle(.secondary)
            }
        }
    }
}

struct FavoriteRow: View {
    @Environment(Store.self) private var store
    let id: String
    let pinned: Bool
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 4) {
            StationRow(id: id, pinned: pinned)
            Button { withAnimation { store.remove(id) } } label: {
                Image(systemName: "minus.circle.fill")
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .opacity(hovering ? 1 : 0)
            .help("Remove")
        }
        .padding(.horizontal, 4)
        .padding(.vertical, 2)
        .background(hovering ? Color.primary.opacity(0.06) : .clear, in: RoundedRectangle(cornerRadius: 6))
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .onTapGesture { store.openInMaps(id) }
        .contextMenu {
            if !pinned || store.showNearest { Button("Pin to Menu Bar") { store.pin(id) } }
            Button("Open in Maps") { store.openInMaps(id) }
            Divider()
            Button("Remove", role: .destructive) { store.remove(id) }
        }
        .swipeActions(edge: .trailing) {
            Button(role: .destructive) { store.remove(id) } label: { Label("Remove", systemImage: "trash") }
        }
    }
}

/// A nearby (non-favourite) station; clicking adds it to favourites.
struct NearbyRow: View {
    let id: String
    let onAdd: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: onAdd) {
            StationRow(id: id, trailingIcon: "plus.circle.fill")
                .padding(.horizontal, 8)
                .padding(.vertical, 5)
                .background(hovering ? Color.primary.opacity(0.06) : .clear, in: RoundedRectangle(cornerRadius: 6))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .help("Add to favourites")
    }
}

struct Count: View {
    let icon: String
    let value: Int
    let tint: Color

    var body: some View {
        HStack(spacing: 2) {
            Image(systemName: icon).font(.caption).foregroundStyle(value > 0 ? tint : .secondary)
            Text("\(value)").monospacedDigit().fontWeight(.semibold)
                .foregroundStyle(value > 0 ? .primary : .secondary)
        }
        .frame(minWidth: 34, alignment: .trailing)
    }
}

struct StatusDot: View {
    let live: LiveStation?

    var body: some View {
        Circle().fill(color).frame(width: 8, height: 8)
    }

    private var color: Color {
        guard let live, live.outOfService != true else { return .gray.opacity(0.5) }
        switch live.eBikes + live.bikes {
        case 0: return .red
        case 1...2: return .orange
        default: return .green
        }
    }
}

// MARK: - Footer

struct Footer: View {
    @Environment(Store.self) private var store
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled

    var body: some View {
        @Bindable var store = store
        HStack(spacing: 8) {
            if let error = store.error {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange).lineLimit(1)
            } else if let t = store.lastUpdated {
                TimelineView(.periodic(from: .now, by: 15)) { _ in
                    Text("Updated \(t, format: .relative(presentation: .named))")
                }
                .foregroundStyle(.secondary)
            }
            Spacer()
            Button { Task { await store.refresh() } } label: {
                if store.isLoading {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "arrow.clockwise")
                }
            }
            .buttonStyle(.borderless)
            .help("Refresh now")
            Menu {
                Picker("Menu Bar Shows", selection: $store.showNearest) {
                    Text("Nearest Favourite").tag(true)
                    Text("First Favourite (pinned)").tag(false)
                }
                .pickerStyle(.inline)
                if store.showNearest && store.locationDenied {
                    Button("Allow Location Access…") {
                        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_LocationServices")!)
                    }
                }
                Divider()
                Toggle("Launch at Login", isOn: $launchAtLogin)
                Divider()
                Button("Quit PubliBike") { NSApp.terminate(nil) }
            } label: {
                Image(systemName: "gearshape")
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
        }
        .font(.caption)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .onChange(of: launchAtLogin) { _, on in
            do {
                if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            } catch {
                launchAtLogin = SMAppService.mainApp.status == .enabled
            }
        }
    }
}
