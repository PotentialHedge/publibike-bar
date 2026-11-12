// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "PubliBikeBar",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "PubliBikeBar",
            path: "Sources/PubliBikeBar",
            swiftSettings: [.swiftLanguageMode(.v5)]
        )
    ]
)
