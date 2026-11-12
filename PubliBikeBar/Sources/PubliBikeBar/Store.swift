import AppKit
import CoreLocation
import Foundation
import Observation

@MainActor
@Observable
final class Store {
    private(set) var live: [String: LiveStation] = [:]
    private(set) var info: [String: StationInfo] = [:]
    private(set) var favorites: [String] = []
    private(set) var lastUpdated: Date?
    private(set) var error: String?
    private(set) var isLoading = false
    private(set) var location: CLLocation?
    private(set) var locationDenied = false

    /// Menu bar shows the nearest favourite (true) or the first one (false).
    var showNearest: Bool = UserDefaults.standard.object(forKey: "showNearest") as? Bool ?? true {
        didSet {
            UserDefaults.standard.set(showNearest, forKey: "showNearest")
            showNearest ? locator.start() : locator.stop()
        }
    }

    @ObservationIgnored private let locator = LocationProvider()
    @ObservationIgnored private var searchIndex: [(id: String, name: String, hay: String)] = []
    @ObservationIgnored private var pollTask: Task<Void, Never>?
    @ObservationIgnored private var infoFetchedAt: Date?

    private static let favoritesKey = "favorites"
    private static let pollInterval: Duration = .seconds(60)
    private static let infoMaxAge: TimeInterval = 24 * 3600

    private let cacheDir: URL = {
        let dir = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("PubliBikeBar", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }()
    private var liveCache: URL { cacheDir.appendingPathComponent("live.json") }
    private var infoCache: URL { cacheDir.appendingPathComponent("info.json") }

    init() {
        loadFavorites()
        loadCaches()
        startPolling()
        locator.onUpdate = { [weak self] loc in self?.location = loc }
        locator.onAuthorizationChange = { [weak self] ok in
            guard let self else { return }
            self.locationDenied = self.locator.isDenied
        }
        if showNearest { locator.start() }
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in await self?.refresh() }
        }
    }

    // MARK: Favourites

    private func loadFavorites() {
        if let saved = UserDefaults.standard.stringArray(forKey: Self.favoritesKey) {
            favorites = saved
            return
        }
        // One-time import from the old SwiftBar plugin.
        let legacy = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/publibike/favorites.json")
        if let data = try? Data(contentsOf: legacy),
           let ids = try? JSONDecoder().decode([String].self, from: data) {
            favorites = ids
            saveFavorites()
        }
    }

    private func saveFavorites() {
        UserDefaults.standard.set(favorites, forKey: Self.favoritesKey)
    }

    func add(_ id: String) {
        guard !favorites.contains(id) else { return }
        favorites.append(id)
        saveFavorites()
    }

    func remove(_ id: String) {
        favorites.removeAll { $0 == id }
        saveFavorites()
    }

    func move(from source: IndexSet, to destination: Int) {
        favorites.move(fromOffsets: source, toOffset: destination)
        saveFavorites()
    }

    /// Pin a station to the menu bar: move it to the top and stop following location.
    func pin(_ id: String) {
        showNearest = false
        guard let i = favorites.firstIndex(of: id), i > 0 else { return }
        favorites.remove(at: i)
        favorites.insert(id, at: 0)
        saveFavorites()
    }

    /// The favourite shown in the menu bar.
    var menuBarStation: String? {
        if showNearest, let here = location {
            let nearest = favorites.min { a, b in
                (distance(to: a, from: here) ?? .infinity) < (distance(to: b, from: here) ?? .infinity)
            }
            if let nearest, distance(to: nearest, from: here) != nil { return nearest }
        }
        return favorites.first
    }

    /// Closest in-service stations that aren't favourites yet (empty without a location).
    func nearby(limit: Int) -> [String] {
        guard limit > 0, let here = location else { return [] }
        let fav = Set(favorites)
        return info.values
            .filter { !fav.contains($0.id) && live[$0.id].map { $0.outOfService != true } == true }
            .map { ($0.id, here.distance(from: CLLocation(latitude: $0.lat, longitude: $0.lon))) }
            .sorted { $0.1 < $1.1 }
            .prefix(limit).map(\.0)
    }

    func distance(to id: String, from here: CLLocation? = nil) -> CLLocationDistance? {
        guard let here = here ?? location, let s = info[id] else { return nil }
        return here.distance(from: CLLocation(latitude: s.lat, longitude: s.lon))
    }

    func formattedDistance(to id: String) -> String? {
        guard let d = distance(to: id) else { return nil }
        if d < 1000 { return "\(Int((d / 10).rounded()) * 10) m" }
        return String(format: d < 10_000 ? "%.1f km" : "%.0f km", d / 1000)
    }

    // MARK: Data

    private func loadCaches() {
        if let data = try? Data(contentsOf: infoCache),
           let decoded = try? JSONDecoder().decode([String: StationInfo].self, from: data) {
            setInfo(decoded)
            infoFetchedAt = (try? infoCache.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate
        }
        if let data = try? Data(contentsOf: liveCache), let decoded = try? API.decodeLive(data) {
            live = decoded
            lastUpdated = (try? liveCache.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate
        }
    }

    private func setInfo(_ new: [String: StationInfo]) {
        info = new
        searchIndex = new.values.map { s in
            (s.id, s.name, Self.fold("\(s.name) \(s.address)"))
        }.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    private func startPolling() {
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: Self.pollInterval)
            }
        }
    }

    /// Called when the panel opens: refresh only if the data is older than 20 s.
    func refreshIfStale() {
        if showNearest { locator.requestNow() }
        if let t = lastUpdated, Date().timeIntervalSince(t) < 20, error == nil { return }
        Task { await refresh() }
    }

    func refresh() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }

        if info.isEmpty || Date().timeIntervalSince(infoFetchedAt ?? .distantPast) > Self.infoMaxAge {
            if let fresh = try? await API.fetchInfo() {
                setInfo(fresh)
                infoFetchedAt = Date()
                if let data = try? JSONEncoder().encode(fresh) { try? data.write(to: infoCache) }
            }
        }

        do {
            let data = try await API.fetch(API.liveURL)
            live = try API.decodeLive(data)
            lastUpdated = Date()
            error = nil
            try? data.write(to: liveCache)
        } catch {
            self.error = (error as? URLError)?.localizedDescription ?? error.localizedDescription
        }
    }

    // MARK: Search

    static func fold(_ s: String) -> String {
        s.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil)
    }

    func search(_ query: String, limit: Int = 8) -> [String] {
        let terms = Self.fold(query).split(separator: " ").map(String.init)
        guard !terms.isEmpty else { return [] }
        var scored: [(score: Int, id: String)] = []
        for entry in searchIndex where !favorites.contains(entry.id) {
            guard terms.allSatisfy({ entry.hay.contains($0) }) else { continue }
            // Per term: whole word 0, word prefix 1, inside a word 3. Name start gets a bonus.
            let words = entry.hay.split(whereSeparator: { !$0.isLetter && !$0.isNumber })
            var score = 0
            for t in terms {
                if words.contains(where: { $0 == t }) { score += 0 }
                else if words.contains(where: { $0.hasPrefix(t) }) { score += 1 }
                else { score += 3 }
            }
            if !Self.fold(entry.name).hasPrefix(terms[0]) { score += 1 }
            scored.append((score, entry.id))
        }
        // Ties: nearer stations first if we know where you are, else alphabetical (index order).
        let here = location
        return scored.enumerated()
            .map { (score: $0.element.score, dist: distance(to: $0.element.id, from: here) ?? 0, order: $0.offset, id: $0.element.id) }
            .sorted { ($0.score, $0.dist, $0.order) < ($1.score, $1.dist, $1.order) }
            .prefix(limit).map(\.id)
    }

    // MARK: Helpers

    func info(for id: String) -> StationInfo? { info[id] }

    func title(for id: String) -> String { info[id]?.title ?? "Station \(id)" }

    func openInMaps(_ id: String) {
        guard let s = info[id] else { return }
        var c = URLComponents(string: "https://maps.apple.com/")!
        c.queryItems = [.init(name: "q", value: s.name), .init(name: "ll", value: "\(s.lat),\(s.lon)")]
        if let url = c.url { NSWorkspace.shared.open(url) }
    }
}
