#include "vault/vault_manager.hpp"
#include <fstream>
#include <iostream>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace motherbrain {
namespace vault {

VaultManager::VaultManager(const fs::path& vault_root)
    : vault_root_(vault_root) {
}

VaultManager::~VaultManager() {
    if (db_) {
        sqlite3_close(db_);
    }
}

bool VaultManager::initialize() {
    // Create vault directory structure if it doesn't exist
    std::vector<fs::path> dirs = {
        vault_root_ / "projects",
        vault_root_ / "shared" / "base_models",
        vault_root_ / "shared" / "global_datasets"
    };

    for (const auto& dir : dirs) {
        std::error_code ec;
        fs::create_directories(dir, ec);
        if (ec) {
            std::cerr << "[VAULT] Failed to create directory: " << dir << " - " << ec.message() << std::endl;
            return false;
        }
    }

    // Open SQLite database
    fs::path db_path = vault_root_ / "vault_index.db";
    int rc = sqlite3_open(db_path.c_str(), &db_);
    if (rc != SQLITE_OK) {
        std::cerr << "[VAULT] Failed to open database: " << sqlite3_errmsg(db_) << std::endl;
        return false;
    }

    // Enable WAL mode for concurrent reads
    sqlite3_exec(db_, "PRAGMA journal_mode=WAL;", nullptr, nullptr, nullptr);

    if (!create_tables()) {
        return false;
    }

    std::cout << "[VAULT] Initialized at: " << vault_root_ << std::endl;
    return reindex_all();
}

bool VaultManager::create_tables() {
    const char* sql = R"(
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            status TEXT,
            path TEXT NOT NULL,
            tags TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT,
            project_id TEXT,
            type TEXT,
            chip TEXT,
            protocol TEXT,
            capabilities TEXT,
            PRIMARY KEY (device_id, project_id),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS models (
            model_id TEXT,
            project_id TEXT,
            base_model TEXT,
            role TEXT,
            lora_adapter_path TEXT,
            quantization TEXT,
            PRIMARY KEY (model_id, project_id),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS datasets (
            name TEXT,
            project_id TEXT,
            source TEXT,
            format TEXT,
            path TEXT,
            size INTEGER,
            tags TEXT,
            PRIMARY KEY (name, project_id),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS simulation_runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT,
            engine TEXT,
            date TEXT,
            success_rate REAL,
            results_path TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS projects_fts USING fts5(
            id, name, description, tags, content='projects', content_rowid='rowid'
        );
    )";

    char* err_msg = nullptr;
    int rc = sqlite3_exec(db_, sql, nullptr, nullptr, &err_msg);
    if (rc != SQLITE_OK) {
        std::cerr << "[VAULT] SQL error: " << err_msg << std::endl;
        sqlite3_free(err_msg);
        return false;
    }

    return true;
}

void VaultManager::clear_index() {
    sqlite3_exec(db_, "DELETE FROM simulation_runs;", nullptr, nullptr, nullptr);
    sqlite3_exec(db_, "DELETE FROM datasets;", nullptr, nullptr, nullptr);
    sqlite3_exec(db_, "DELETE FROM models;", nullptr, nullptr, nullptr);
    sqlite3_exec(db_, "DELETE FROM devices;", nullptr, nullptr, nullptr);
    sqlite3_exec(db_, "DELETE FROM projects_fts;", nullptr, nullptr, nullptr);
    sqlite3_exec(db_, "DELETE FROM projects;", nullptr, nullptr, nullptr);
}

bool VaultManager::reindex_all() {
    clear_index();

    fs::path projects_dir = vault_root_ / "projects";
    if (!fs::exists(projects_dir)) {
        std::cout << "[VAULT] No projects directory found. Skipping index." << std::endl;
        return true;
    }

    int count = 0;
    for (const auto& entry : fs::directory_iterator(projects_dir)) {
        if (entry.is_directory()) {
            fs::path manifest = entry.path() / "manifest.json";
            if (fs::exists(manifest)) {
                if (parse_and_index_manifest(manifest)) {
                    count++;
                }
            }
        }
    }

    std::cout << "[VAULT] Indexed " << count << " project(s)" << std::endl;
    return true;
}

bool VaultManager::parse_and_index_manifest(const fs::path& manifest_path) {
    std::ifstream file(manifest_path);
    if (!file.is_open()) {
        std::cerr << "[VAULT] Cannot open manifest: " << manifest_path << std::endl;
        return false;
    }

    json manifest;
    try {
        file >> manifest;
    } catch (const json::parse_error& e) {
        std::cerr << "[VAULT] JSON parse error in " << manifest_path << ": " << e.what() << std::endl;
        return false;
    }

    // Project info
    std::string project_id = manifest["project"]["id"].get<std::string>();
    std::string project_name = manifest["project"]["name"].get<std::string>();
    std::string description = manifest["project"].value("description", "");
    std::string status = manifest["project"].value("status", "design");
    std::string created = manifest["project"].value("created", "");
    std::string updated = manifest["project"].value("updated", "");

    std::string tags_str;
    if (manifest["project"].contains("tags") && manifest["project"]["tags"].is_array()) {
        for (const auto& tag : manifest["project"]["tags"]) {
            if (!tags_str.empty()) tags_str += ",";
            tags_str += tag.get<std::string>();
        }
    }

    std::string project_path = manifest_path.parent_path().string();

    // Insert project
    std::string insert_project = 
        "INSERT OR REPLACE INTO projects (id, name, description, status, path, tags, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?);";

    sqlite3_stmt* stmt;
    sqlite3_prepare_v2(db_, insert_project.c_str(), -1, &stmt, nullptr);
    sqlite3_bind_text(stmt, 1, project_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, project_name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 3, description.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 4, status.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 5, project_path.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 6, tags_str.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 7, created.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 8, updated.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);

    // FTS index
    std::string insert_fts =
        "INSERT OR REPLACE INTO projects_fts (rowid, id, name, description, tags) "
        "VALUES ((SELECT rowid FROM projects WHERE id = ?), ?, ?, ?, ?);";
    sqlite3_prepare_v2(db_, insert_fts.c_str(), -1, &stmt, nullptr);
    sqlite3_bind_text(stmt, 1, project_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, project_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 3, project_name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 4, description.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 5, tags_str.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);

    // Devices
    if (manifest["hardware"].contains("devices")) {
        for (const auto& dev : manifest["hardware"]["devices"]) {
            std::string dev_id = dev["device_id"].get<std::string>();
            std::string type = dev.value("type", "unknown");
            std::string chip = dev.value("chip", "unknown");
            std::string protocol = dev["communication"].value("protocol", "unknown");

            std::string caps;
            if (dev.contains("capabilities") && dev["capabilities"].is_array()) {
                for (const auto& cap : dev["capabilities"]) {
                    if (!caps.empty()) caps += ",";
                    caps += cap.get<std::string>();
                }
            }

            std::string insert_dev =
                "INSERT OR REPLACE INTO devices (device_id, project_id, type, chip, protocol, capabilities) "
                "VALUES (?, ?, ?, ?, ?, ?);";
            sqlite3_prepare_v2(db_, insert_dev.c_str(), -1, &stmt, nullptr);
            sqlite3_bind_text(stmt, 1, dev_id.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 2, project_id.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 3, type.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 4, chip.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 5, protocol.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 6, caps.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_step(stmt);
            sqlite3_finalize(stmt);
        }
    }

    // Models
    if (manifest["ai"].contains("models")) {
        for (const auto& model : manifest["ai"]["models"]) {
            std::string model_id = model["model_id"].get<std::string>();
            std::string base = model.value("base_model", "unknown");
            std::string role = model.value("role", "general");
            std::string lora = model.value("lora_adapter_path", "");
            std::string quant = model.value("quantization", "none");

            std::string insert_model =
                "INSERT OR REPLACE INTO models (model_id, project_id, base_model, role, lora_adapter_path, quantization) "
                "VALUES (?, ?, ?, ?, ?, ?);";
            sqlite3_prepare_v2(db_, insert_model.c_str(), -1, &stmt, nullptr);
            sqlite3_bind_text(stmt, 1, model_id.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 2, project_id.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 3, base.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 4, role.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 5, lora.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 6, quant.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_step(stmt);
            sqlite3_finalize(stmt);
        }
    }

    // Datasets
    if (manifest["datasets"].contains("collections")) {
        for (const auto& ds : manifest["datasets"]["collections"]) {
            std::string ds_name = ds["name"].get<std::string>();
            std::string source = ds.value("source", "unknown");
            std::string format = ds.value("format", "unknown");
            std::string ds_path = ds.value("path", "");
            int size = ds.value("size", 0);

            std::string ds_tags;
            if (ds.contains("tags") && ds["tags"].is_array()) {
                for (const auto& tag : ds["tags"]) {
                    if (!ds_tags.empty()) ds_tags += ",";
                    ds_tags += tag.get<std::string>();
                }
            }

            std::string insert_ds =
                "INSERT OR REPLACE INTO datasets (name, project_id, source, format, path, size, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?);";
            sqlite3_prepare_v2(db_, insert_ds.c_str(), -1, &stmt, nullptr);
            sqlite3_bind_text(stmt, 1, ds_name.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 2, project_id.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 3, source.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 4, format.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 5, ds_path.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_int(stmt, 6, size);
            sqlite3_bind_text(stmt, 7, ds_tags.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_step(stmt);
            sqlite3_finalize(stmt);
        }
    }

    // Simulation runs
    if (manifest["simulation"].contains("environments")) {
        for (const auto& env : manifest["simulation"]["environments"]) {
            if (env.contains("training_runs")) {
                for (const auto& run : env["training_runs"]) {
                    std::string run_id = run["run_id"].get<std::string>();
                    std::string engine = env["engine"].get<std::string>();
                    std::string date = run.value("date", "");
                    double success_rate = run["metrics"].value("success_rate", 0.0);
                    std::string results = run.value("results_path", "");

                    std::string insert_run =
                        "INSERT OR REPLACE INTO simulation_runs (run_id, project_id, engine, date, success_rate, results_path) "
                        "VALUES (?, ?, ?, ?, ?, ?);";
                    sqlite3_prepare_v2(db_, insert_run.c_str(), -1, &stmt, nullptr);
                    sqlite3_bind_text(stmt, 1, run_id.c_str(), -1, SQLITE_TRANSIENT);
                    sqlite3_bind_text(stmt, 2, project_id.c_str(), -1, SQLITE_TRANSIENT);
                    sqlite3_bind_text(stmt, 3, engine.c_str(), -1, SQLITE_TRANSIENT);
                    sqlite3_bind_text(stmt, 4, date.c_str(), -1, SQLITE_TRANSIENT);
                    sqlite3_bind_double(stmt, 5, success_rate);
                    sqlite3_bind_text(stmt, 6, results.c_str(), -1, SQLITE_TRANSIENT);
                    sqlite3_step(stmt);
                    sqlite3_finalize(stmt);
                }
            }
        }
    }

    return true;
}

// Query implementations
std::vector<ProjectInfo> VaultManager::list_projects() const {
    std::vector<ProjectInfo> results;
    const char* sql = "SELECT id, name, description, status, path FROM projects;";
    sqlite3_stmt* stmt;
    sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr);

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        ProjectInfo info;
        info.id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
        info.name = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
        info.description = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2) ? reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)) : "");
        info.status = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3));
        info.path = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4));
        results.push_back(info);
    }
    sqlite3_finalize(stmt);
    return results;
}

std::optional<ProjectInfo> VaultManager::get_project(const std::string& project_id) const {
    const char* sql = "SELECT id, name, description, status, path FROM projects WHERE id = ?;";
    sqlite3_stmt* stmt;
    sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr);
    sqlite3_bind_text(stmt, 1, project_id.c_str(), -1, SQLITE_TRANSIENT);

    std::optional<ProjectInfo> result;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        ProjectInfo info;
        info.id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
        info.name = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
        info.description = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2) ? reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)) : "");
        info.status = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3));
        info.path = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4));
        result = info;
    }
    sqlite3_finalize(stmt);
    return result;
}

std::vector<DeviceInfo> VaultManager::get_devices(const std::string& project_id) const {
    std::vector<DeviceInfo> results;
    const char* sql = "SELECT device_id, type, chip, protocol, project_id FROM devices WHERE project_id = ?;";
    sqlite3_stmt* stmt;
    sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr);
    sqlite3_bind_text(stmt, 1, project_id.c_str(), -1, SQLITE_TRANSIENT);

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        DeviceInfo info;
        info.device_id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
        info.type = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
        info.chip = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2));
        info.protocol = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3));
        info.project_id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4));
        results.push_back(info);
    }
    sqlite3_finalize(stmt);
    return results;
}

std::vector<ModelInfo> VaultManager::get_models(const std::string& project_id) const {
    std::vector<ModelInfo> results;
    const char* sql = "SELECT model_id, base_model, role, lora_adapter_path, project_id FROM models WHERE project_id = ?;";
    sqlite3_stmt* stmt;
    sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr);
    sqlite3_bind_text(stmt, 1, project_id.c_str(), -1, SQLITE_TRANSIENT);

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        ModelInfo info;
        info.model_id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
        info.base_model = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
        info.role = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2));
        info.lora_adapter_path = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3) ? reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3)) : "");
        info.project_id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4));
        results.push_back(info);
    }
    sqlite3_finalize(stmt);
    return results;
}

std::vector<DatasetInfo> VaultManager::get_datasets(const std::string& project_id) const {
    std::vector<DatasetInfo> results;
    const char* sql = "SELECT name, source, format, path, project_id FROM datasets WHERE project_id = ?;";
    sqlite3_stmt* stmt;
    sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr);
    sqlite3_bind_text(stmt, 1, project_id.c_str(), -1, SQLITE_TRANSIENT);

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        DatasetInfo info;
        info.name = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
        info.source = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
        info.format = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2));
        info.path = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3) ? reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3)) : "");
        info.project_id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4));
        results.push_back(info);
    }
    sqlite3_finalize(stmt);
    return results;
}

std::vector<ProjectInfo> VaultManager::search(const std::string& query) const {
    std::vector<ProjectInfo> results;
    std::string sql = "SELECT p.id, p.name, p.description, p.status, p.path "
                      "FROM projects p "
                      "JOIN projects_fts fts ON p.rowid = fts.rowid "
                      "WHERE projects_fts MATCH ?;";
    sqlite3_stmt* stmt;
    sqlite3_prepare_v2(db_, sql.c_str(), -1, &stmt, nullptr);
    sqlite3_bind_text(stmt, 1, query.c_str(), -1, SQLITE_TRANSIENT);

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        ProjectInfo info;
        info.id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
        info.name = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
        info.description = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2) ? reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)) : "");
        info.status = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3));
        info.path = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4));
        results.push_back(info);
    }
    sqlite3_finalize(stmt);
    return results;
}

} // namespace vault
} // namespace motherbrain

