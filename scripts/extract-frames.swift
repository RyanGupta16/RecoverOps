// Extracts an evenly-spaced JPEG frame sequence from a video, for canvas
// scroll-scrubbing on the marketing page.
//
// Scrubbing a <video> by setting currentTime stutters badly on H.264 with
// sparse keyframes — the decoder has to seek to the previous I-frame and
// re-decode forward on every scroll tick. A pre-extracted frame sequence turns
// scrubbing into an array lookup and a drawImage call.
//
// Uses AVFoundation, so it needs no ffmpeg and no Homebrew.
//
// Usage: swift scripts/extract-frames.swift <input.mp4> <outDir> <count> <width> <quality>

import AVFoundation
import AppKit
import Foundation

let args = CommandLine.arguments
guard args.count >= 6,
      let frameCount = Int(args[3]),
      let targetWidth = Double(args[4]),
      let quality = Double(args[5])
else {
    FileHandle.standardError.write("usage: extract-frames.swift <input> <outDir> <count> <width> <quality>\n".data(using: .utf8)!)
    exit(1)
}

let inputURL = URL(fileURLWithPath: args[1])
let outDir = URL(fileURLWithPath: args[2])

try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

let asset = AVURLAsset(url: inputURL)
let durationSeconds = CMTimeGetSeconds(asset.duration)
guard durationSeconds > 0 else {
    FileHandle.standardError.write("could not read asset duration\n".data(using: .utf8)!)
    exit(1)
}

let generator = AVAssetImageGenerator(asset: asset)
generator.appliesPreferredTrackTransform = true
// Exact frames: without zero tolerance the generator snaps to keyframes and the
// sequence judders in exactly the way we are trying to avoid.
generator.requestedTimeToleranceBefore = .zero
generator.requestedTimeToleranceAfter = .zero
generator.maximumSize = CGSize(width: targetWidth, height: 0)

var totalBytes = 0

for index in 0..<frameCount {
    let progress = frameCount == 1 ? 0 : Double(index) / Double(frameCount - 1)
    // Stop a hair short of the final timestamp — asking for exactly `duration`
    // fails on some encodes.
    let seconds = progress * (durationSeconds - 0.04)
    let time = CMTime(seconds: seconds, preferredTimescale: 600)

    do {
        let cgImage = try generator.copyCGImage(at: time, actualTime: nil)
        let rep = NSBitmapImageRep(cgImage: cgImage)
        guard let data = rep.representation(
            using: .jpeg,
            properties: [.compressionFactor: NSNumber(value: quality)]
        ) else {
            FileHandle.standardError.write("frame \(index): jpeg encode failed\n".data(using: .utf8)!)
            exit(1)
        }
        let name = String(format: "frame-%03d.jpg", index)
        try data.write(to: outDir.appendingPathComponent(name))
        totalBytes += data.count
    } catch {
        FileHandle.standardError.write("frame \(index) at \(seconds)s: \(error)\n".data(using: .utf8)!)
        exit(1)
    }
}

let mb = Double(totalBytes) / 1_048_576.0
print(String(format: "%d frames · %.2f MB total · %.0f KB average", frameCount, mb, Double(totalBytes) / Double(frameCount) / 1024.0))
