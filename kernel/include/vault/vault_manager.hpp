#pragma once

#include <sqlite3.h>
#include <string>
#include <vector>
#include <filesystem>
#include <optional>

namespace motherbrain {
namespace vault {

namespace fs = std::filesystem;

struct ProjectInfo {
    std::string id;
    std::string name;
    std::string description;
    std::string status;
    std::string path;
};

struct DeviceInfo {
    std::string device_id;
    std::string type;
    std::string chip;
    std::string protocol;
    std::string project_id;
};

struct ModelInfo {
    std::string model_id;
    std::string base_model;
    std::string role;
    std::string lora_adapter_path;
    std::string project_id;
};

struct DatasetInfo {
    std::string name;
    std::string source;
    std::string format;
    std::string path;
    std::string project_id;
};

class VaultManager {
public:
    explicit VaultManager(const fs::path& vault_root);
    ~VaultManager();

    bool initialize();
    bool reindex_all();

    std::vector<ProjectInfo> list_projects() const;
    std::optional<ProjectInfo> get_project(const std::string& project_id) const;
    std::vector<DeviceInfo> get_devices(const std::string& project_id) const;
    std::vector<ModelInfo> get_models(const std::string& project_id) const;
    std::vector<DatasetInfo> get_datasets(const std::string& project_id) const;
    std::vector<ProjectInfo> search(const std::string& query) const;

    const fs::path& root_path() const { return vault_root_; }

private:
    bool create_tables();
    bool parse_and_index_manifest(const fs::path& manifest_path);
    void clear_index();

    fs::path vault_root_;
    sqlite3* db_ = nullptr;
};

} // namespace vault
} // namespace motherbrain
