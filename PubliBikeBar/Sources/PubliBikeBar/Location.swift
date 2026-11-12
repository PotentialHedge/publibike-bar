import CoreLocation

/// Thin wrapper around CLLocationManager that reports the latest location on the main actor.
@MainActor
final class LocationProvider: NSObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    var onUpdate: ((CLLocation?) -> Void)?
    var onAuthorizationChange: ((Bool) -> Void)?

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        manager.distanceFilter = 50
    }

    var isAuthorized: Bool {
        let s = manager.authorizationStatus
        return s == .authorizedAlways || s == .authorized
    }

    var isDenied: Bool {
        let s = manager.authorizationStatus
        return s == .denied || s == .restricted
    }

    func start() {
        switch manager.authorizationStatus {
        case .notDetermined:
            manager.requestWhenInUseAuthorization()
        case .denied, .restricted:
            onUpdate?(nil)
        default:
            manager.startUpdatingLocation()
        }
    }

    func stop() {
        manager.stopUpdatingLocation()
        onUpdate?(nil)
    }

    /// One-shot refresh, e.g. when the panel opens.
    func requestNow() {
        if isAuthorized { manager.requestLocation() }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { @MainActor in
            self.onAuthorizationChange?(self.isAuthorized)
            if self.isAuthorized { self.manager.startUpdatingLocation() }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        Task { @MainActor in self.onUpdate?(loc) }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        // Keep the last known location; a transient failure shouldn't flip the menu bar.
    }
}
