
#ifndef FPGA_MANAGER_H
#define FPGA_MANAGER_H

#include <iostream>
#include <fstream>
#include <string>
#include <vector>
#include <unordered_map>
#include <sstream>
#include <limits>
#include <assert.h>

class FPGAManager {
public:
    std::unordered_map<std::string, int> name_to_id;
    std::vector<std::string> id_to_name;
    std::vector<std::vector<int>> fpga_info;
    std::vector<std::vector<int>> adj_matrix;
    std::vector<std::vector<int>> hop_distances;
    int max_hop_distance;
    std::vector<double> resource_averages;   
    std::vector<int> resource_minimums;      
    std::vector<int> resource_maximums;      
    int fpga_num;



    int get_or_assign_id(const std::string& fpga_name) {
        auto it = name_to_id.find(fpga_name);
        if (it != name_to_id.end()) {
            return it->second;
        } else {
            int new_id = id_to_name.size();
            name_to_id[fpga_name] = new_id;
            id_to_name.push_back(fpga_name);
            fpga_info.emplace_back(std::vector<int>(9, 0)); 
            adj_matrix.emplace_back(std::vector<int>(id_to_name.size(), 0));
            for(auto& row : adj_matrix) {
                if(row.size() < id_to_name.size()) {
                    row.resize(id_to_name.size(), 0);
                }
            }
            return new_id;
        }
    }

    FPGAManager() : max_hop_distance(0) {}

    bool readDesignInfo(const std::string& filepath) {
        std::ifstream infile(filepath);
        if (!infile) {
            std::cerr << "Error: Cannot open file \"" << filepath << "\"." << std::endl;
            return false;
        }

        std::string line;
        int line_number = 0;

        while (std::getline(infile, line)) {
            line_number++;
            if (line.empty()) continue; 

            std::istringstream iss(line);
            std::string fpga_label;
            int max_connections;
            std::vector<int> resources(8, 0);

            if (!(iss >> fpga_label)) {
                std::cerr << "Error: Line " << line_number << " lacks FPGA label." << std::endl;
                return false;
            }

            if (!(iss >> max_connections)) {
                std::cerr << "Error: Line " << line_number << " lacks the maximum number of external connections." << std::endl;
                return false;
            }

            bool resource_read_error = false;
            for(int i = 0; i < 8; ++i){
                if (!(iss >> resources[i])) {
                    std::cerr << "Error: Line " << line_number << " lacks the maximum available amount of resource " << (i+1) << "." << std::endl;
                    resource_read_error = true;
                    break;
                }
            }
            if (resource_read_error) {
                return false;
            }

            int fpga_id = get_or_assign_id(fpga_label);

            fpga_info[fpga_id][0] = max_connections;
            for(int i = 0; i < 8; ++i){
                fpga_info[fpga_id][i+1] = resources[i];
            }
        }

        fpga_num = id_to_name.size();
        infile.close();
        return true;
    }



    bool readDesignTopo(const std::string& filepath) {
        std::ifstream infile(filepath);
        if (!infile) {
            std::cerr << "Error: Cannot open file \"" << filepath << "\"." << std::endl;
            return false;
        }

        std::string line;
        int line_number = 0;

        if (!std::getline(infile, line)) {
            std::cerr << "Error: design.topo file is empty." << std::endl;
            return false;
        }
        line_number++;
        try {
            max_hop_distance = std::stoi(line);
        } catch (const std::exception& e) {
            std::cerr << "Error: The first line must be an integer representing the maximum hop distance." << std::endl;
            return false;
        }

        while (std::getline(infile, line)) {
            line_number++;
            if (line.empty()) continue; 

            std::istringstream iss(line);
            int fpga1_id, fpga2_id;
            std::string fpga1, fpga2;

            if (!(iss >> fpga1 >> fpga2)) {
                std::cerr << "Error: Line " << line_number << " has an incorrect format, should be two FPGA names." << std::endl;
                return false;
            }

            auto it1 = name_to_id.find(fpga1);
            auto it2 = name_to_id.find(fpga2);

            if (it1 == name_to_id.end() || it2 == name_to_id.end()) {
                std::cerr << "Error: Line " << line_number << " contains FPGA names that are not defined in design.info." << std::endl;
                return false;
            }

            fpga1_id = it1->second;
            fpga2_id = it2->second;

            adj_matrix[fpga1_id][fpga2_id] = 1;
            adj_matrix[fpga2_id][fpga1_id] = 1; 
        }

        infile.close();
        return true;
    }



    bool computeHopDistances() {
        int n = id_to_name.size();
        if(n == 0){
            std::cerr << "Error: No FPGA data available for hop distance calculation." << std::endl;
            return false;
        }

        hop_distances.assign(n, std::vector<int>(n, std::numeric_limits<int>::max() / 2));

        for(int i = 0; i < n; ++i){
            hop_distances[i][i] = 0;
            for(int j = 0; j < n; ++j){
                if(adj_matrix[i][j]){
                    hop_distances[i][j] = 1;
                }
            }
        }

        for(int k = 0; k < n; ++k){
            for(int i = 0; i < n; ++i){
                if(hop_distances[i][k] == std::numeric_limits<int>::max() / 2) continue; 
                for(int j = 0; j < n; ++j){
                    if(hop_distances[k][j] == std::numeric_limits<int>::max() / 2) continue; 
                    if(hop_distances[i][j] > hop_distances[i][k] + hop_distances[k][j]){
                        hop_distances[i][j] = hop_distances[i][k] + hop_distances[k][j];
                    }
                }
            }
        }

        for(int i = 0; i < n; ++i){
            for(int j = 0; j < n; ++j){
                if(hop_distances[i][j] > max_hop_distance){
                    hop_distances[i][j] = -1; 
                }
            }
        }

        return true;
    }

    std::vector<std::string> getFPGAList() const {
        return id_to_name;
    }



    void computeResourceStatistics() {
        const int num_resources = 8;
        int num_fpgas = id_to_name.size();
        
        resource_averages.assign(num_resources, 0.0);
        resource_minimums.assign(num_resources, std::numeric_limits<int>::max());
        resource_maximums.assign(num_resources, std::numeric_limits<int>::min());
        
        if(num_fpgas == 0){
            std::cerr << "Error: No FPGA data available to compute statistics." << std::endl;
            return;
        }
        
        for(const auto& info : fpga_info){
            for(int i = 0; i < num_resources; ++i){
                int resource_value = info[i + 1]; 
                resource_averages[i] += static_cast<double>(resource_value);
                
                if(resource_value < resource_minimums[i]){
                    resource_minimums[i] = resource_value;
                }
                
                if(resource_value > resource_maximums[i]){
                    resource_maximums[i] = resource_value;
                }
            }
        }
        
        for(int i = 0; i < num_resources; ++i){
            resource_averages[i] /= static_cast<double>(num_fpgas);
        }
    }

    bool init(const std::string& design_info_path, const std::string& design_topo_path) {
        if(!readDesignInfo(design_info_path)){
            std::cerr << "初始化失败：无法读取 design.info 文件。" << std::endl;
            return false;
        }

        if(!readDesignTopo(design_topo_path)){
            std::cerr << "初始化失败：无法读取 design.topo 文件。" << std::endl;
            return false;
        }

        if(!computeHopDistances()){
            std::cerr << "初始化失败：计算 hop 距离失败。" << std::endl;
            return false;
        }

        computeResourceStatistics();

        return true;
    }
    
    std::vector<double> getResourceAverages() const {
        return resource_averages;
    }
    std::vector<int> getResourceMinimums() const {
        return resource_minimums;
    }
    std::vector<int> getResourceMaximums() const {
        return resource_maximums;
    }



    std::vector<int> getFPGAInfo(int id) const {
        if(id < 0 || id >= static_cast<int>(fpga_info.size())){
            std::cerr << "Error: Invalid FPGA ID " << id << "." << std::endl;
            return std::vector<int>();
        }
        return fpga_info[id];
    }

    std::string getName(int id) const {
        if(id < 0 || id >= static_cast<int>(id_to_name.size())){
            std::cerr << "Error: Invalid FPGA ID " << id << "." << std::endl;
            return "";
        }
        return id_to_name[id];
    }

    int getHopDistance(int id1, int id2) const {
        if(id1 < 0 || id1 >= static_cast<int>(hop_distances.size()) ||
            id2 < 0 || id2 >= static_cast<int>(hop_distances.size())){
            std::cerr << "Error: Invalid FPGA ID " << id1 << " or " << id2 << "." << std::endl;
            return -1;
        }
        if(hop_distances.empty()){
            std::cerr << "Error: hop_distances matrix has not been computed." << std::endl;
            return -1;
        }
        int distance = hop_distances[id1][id2];
        return distance;
    }

    std::vector<std::vector<int>> getHopDistances() const {
        return hop_distances;
    }

    int getMaxHopDistance() const {
        return max_hop_distance;
    }
};

#endif 
























