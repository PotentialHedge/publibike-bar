import Foundation

/// One station from the app's public live endpoint.
struct LiveStation: Codable, Sendable {
    struct Vehicles: Codable, Sendable {
        let count: Int
        let type: Int
    }

    let id: String
    let latitude: Double
    let longitude: Double
    let outOfService: Bool?
    let availableVehicles: [Vehicles]

    enum CodingKeys: String, CodingKey {
        case id, latitude, longitude
        case outOfService = "out_of_service"
        case availableVehicles = "available_vehicles"
    }

    // App API vehicle types (checked against GBFS):
    // 1 bike, 2 e-bike, 3 bike with own battery, 4 cargo, 5 e-scooter.
    var eBikes: Int { availableVehicles.filter { $0.type == 2 }.reduce(0) { $0 + $1.count } }
    var bikes: Int { availableVehicles.filter { $0.type == 1 || $0.type == 3 }.reduce(0) { $0 + $1.count } }
}

/// Static station metadata (names come from GBFS, the live endpoint has none).
struct StationInfo: Codable, Sendable {
    let id: String
    let name: String
    let address: String
    let lat: Double
    let lon: Double

    /// "Comerge - Zürich" -> ("Comerge", "Zürich")
    var title: String { split.title }
    var place: String { split.place }

    private var split: (title: String, place: String) {
        guard let r = name.range(of: " - ", options: .backwards) else { return (name, "") }
        return (String(name[..<r.lowerBound]), String(name[r.upperBound...]))
    }
}

enum API {
    static let liveURL = URL(string: "https://velospot.info/customer/public/api/pbvsng/stations")!
    static let infoURL = URL(string: "https://api.mobidata-bw.de/sharing/gbfs/v3/velospot_ch/station_information")!

    private static let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.timeoutIntervalForRequest = 20
        c.httpAdditionalHeaders = ["User-Agent": "PubliBikeBar/1.0", "Accept": "application/json"]
        return URLSession(configuration: c)
    }()

    static func fetch(_ url: URL) async throws -> Data {
        let (data, response) = try await session.data(from: url)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw URLError(.badServerResponse, userInfo: [NSLocalizedDescriptionKey: "HTTP \(http.statusCode)"])
        }
        return data
    }

    static func decodeLive(_ data: Data) throws -> [String: LiveStation] {
        let list = try JSONDecoder().decode([LiveStation].self, from: data)
        return Dictionary(list.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
    }

    // GBFS v3 station_information
    private struct GBFS: Decodable {
        struct Text: Decodable { let text: String }
        struct Station: Decodable {
            let station_id: String
            let name: [Text]
            let address: String?
            let lat: Double
            let lon: Double
        }
        struct DataBlock: Decodable { let stations: [Station] }
        let data: DataBlock
    }

    static func fetchInfo() async throws -> [String: StationInfo] {
        let data = try await fetch(infoURL)
        let gbfs = try JSONDecoder().decode(GBFS.self, from: data)
        var out: [String: StationInfo] = [:]
        for s in gbfs.data.stations {
            let id = s.station_id.components(separatedBy: ":").last ?? s.station_id
            out[id] = StationInfo(id: id, name: s.name.first?.text ?? "Station \(id)",
                                  address: s.address ?? "", lat: s.lat, lon: s.lon)
        }
        return out
    }
}
