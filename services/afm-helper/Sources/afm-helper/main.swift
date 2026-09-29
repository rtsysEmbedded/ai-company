// afm-helper: document enrichment with Apple's on-device Foundation Model.
//   afm-helper status          -> {"available": true|false, "reason": "..."}
//   afm-helper enrich < text   -> {"summary": "...", "tags": [...], "keywords": [...]}
// Exit code 2 = model unavailable (callers fall back), 1 = other error.
import Foundation
import FoundationModels

@Generable
struct Enrichment {
    @Guide(description: "One sentence, at most 30 words, saying what this document is about")
    var summary: String
    @Guide(description: "3 to 6 short lowercase topic tags, e.g. uart, dma, rtos, security", .count(3...6))
    var tags: [String]
    @Guide(description: "Up to 10 exact identifiers from the text: function names, registers, part numbers, commands", .maximumCount(10))
    var keywords: [String]
}

func emit(_ obj: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}

func availability() -> (Bool, String) {
    switch SystemLanguageModel.default.availability {
    case .available: return (true, "available")
    case .unavailable(let reason): return (false, String(describing: reason))
    }
}

let args = CommandLine.arguments.dropFirst()
let (ok, reason) = availability()

switch args.first ?? "enrich" {
case "status":
    emit(["available": ok, "reason": reason])
case "enrich":
    guard ok else {
        FileHandle.standardError.write("model unavailable: \(reason)\n".data(using: .utf8)!)
        exit(2)
    }
    let input = String(decoding: FileHandle.standardInput.readDataToEndOfFile(), as: UTF8.self)
    // The on-device model has a small context window; the start of a document is enough to tag it.
    let text = String(input.prefix(6000))
    let session = LanguageModelSession(instructions: """
        You index technical documents (code, datasheets, design notes) for a search engine. \
        Describe the document factually. Never invent identifiers that are not in the text.
        """)
    do {
        let r = try await session.respond(to: "Document:\n\(text)", generating: Enrichment.self)
        emit(["summary": r.content.summary, "tags": r.content.tags, "keywords": r.content.keywords])
    } catch {
        FileHandle.standardError.write("generation failed: \(error)\n".data(using: .utf8)!)
        exit(1)
    }
default:
    FileHandle.standardError.write("usage: afm-helper status | enrich < text\n".data(using: .utf8)!)
    exit(1)
}
