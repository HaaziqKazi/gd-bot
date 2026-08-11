#include <Geode/Geode.hpp>

#include "level_dump.hpp"

#include <algorithm>
#include <charconv>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <string_view>
#include <vector>

using namespace geode::prelude;

namespace {

std::vector<std::string_view> split(std::string_view input, char delimiter) {
    std::vector<std::string_view> parts;
    size_t begin = 0;
    while (begin <= input.size()) {
        const size_t end = input.find(delimiter, begin);
        parts.push_back(input.substr(
            begin, end == std::string_view::npos ? input.size() - begin : end - begin));
        if (end == std::string_view::npos) break;
        begin = end + 1;
    }
    return parts;
}

bool isMoveTrigger(std::string_view object) {
    const auto properties = split(object, ',');
    for (size_t i = 0; i + 1 < properties.size(); i += 2) {
        if (properties[i] == "1" && properties[i + 1] == "901") return true;
    }
    return false;
}

bool writeFile(std::filesystem::path const& path, std::string_view contents) {
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out) return false;
    out.write(contents.data(), static_cast<std::streamsize>(contents.size()));
    return out.good();
}

} // namespace

namespace gdrl {

void dumpRequestedMainLevel() {
    static bool attempted = false;
    if (attempted) return;

    const char* requested = std::getenv("GDRL_DUMP_LEVEL");
    if (!requested || !*requested) return;
    attempted = true;

    int levelID = 0;
    const std::string_view requestedView(requested);
    const auto [end, error] = std::from_chars(
        requestedView.data(), requestedView.data() + requestedView.size(), levelID);
    if (error != std::errc{} || end != requestedView.data() + requestedView.size() || levelID <= 0) {
        log::error("[gdrl] DUMP_LEVEL must be a positive integer, got '{}'", requested);
        return;
    }

    // The second argument is dontGetLevelString. This must be false: true yields
    // a 2-object level that still runs and makes every measurement meaningless.
    auto* level = GameLevelManager::sharedState()->getMainLevel(levelID, false);
    if (!level) {
        log::error("[gdrl] DUMP_LEVEL getMainLevel({}, false) returned null", levelID);
        return;
    }

    std::string decoded(level->m_levelString.c_str(), level->m_levelString.size());
    if (decoded.find(';') == std::string::npos) {
        const gd::string unpacked = ZipUtils::decompressString(level->m_levelString, false, 0);
        decoded.assign(unpacked.c_str(), unpacked.size());
    }

    const auto records = split(decoded, ';');
    size_t objectCount = 0;
    std::string moveTriggers;
    for (size_t i = 1; i < records.size(); ++i) {
        if (records[i].empty()) continue;
        ++objectCount;
        if (isMoveTrigger(records[i])) {
            moveTriggers.append(records[i]);
            moveTriggers.push_back('\n');
        }
    }

    // getMainLevel(id, true) has enough metadata to look valid but only two
    // objects. Refuse to create authoritative-looking evidence from that state.
    if (objectCount < 10) {
        log::error("[gdrl] DUMP_LEVEL {} decoded only {} objects; refusing to write "
                   "an incomplete level dump", levelID, objectCount);
        return;
    }

    const auto saveDir = Mod::get()->getSaveDir();
    const auto fullPath = saveDir / ("level-" + std::to_string(levelID) + ".txt");
    const auto movesPath = saveDir / ("level-" + std::to_string(levelID) + "-move-901.txt");
    if (!writeFile(fullPath, decoded) || !writeFile(movesPath, moveTriggers)) {
        log::error("[gdrl] DUMP_LEVEL failed writing '{}' or '{}'",
                   fullPath.string(), movesPath.string());
        return;
    }

    log::info("[gdrl] DUMP_LEVEL {}: {} objects, {} move triggers; wrote '{}' and '{}'",
              levelID, objectCount,
              static_cast<size_t>(std::count(moveTriggers.begin(), moveTriggers.end(), '\n')),
              fullPath.string(), movesPath.string());
}

} // namespace gdrl
