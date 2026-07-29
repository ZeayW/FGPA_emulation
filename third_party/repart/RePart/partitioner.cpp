#include <iostream>
#include <vector>
#include <unordered_map>
#include <fstream>
#include <sstream>
#include <time.h>
#include <chrono>
#include <unistd.h>
#include <cmath>

#include "./datastructure/hypergraph.h"
#include "./io/hypergraph_readin.h"
#include "./io/fpga_manager.h"

#include "./coarsening/full_coarsener_4thread.h"

#include "./assignment/assignment_4thread_nodes.h"

#include "./refinement_replicate/replication_fast_4thread.h"
#include "./refinement_delete/delete_fast_4thread.h"
#include "./refinement_move/move_fast.h"
#include "./refinement_exchange/exchanger.h"



int main(int argc, char* argv[]) {

    auto start = std::chrono::steady_clock::now();

    std::unordered_map<std::string,int> vertex_name_to_id;
    std::unordered_map<int,std::string> vertex_id_to_name;

    Hypergraph hypergraph_init;

    std::string inputDirectory;
    std::string outputFile;
    bool enableReplication = true;

    int opt;
    while ((opt = getopt(argc, argv, "t:s:r:")) != -1) {
        switch (opt) {
            case 't':
                inputDirectory = optarg;
                break;
            case 's':
                outputFile = optarg;
                break;
            case 'r':
                enableReplication = std::stoi(optarg) != 0;
                break;
            default:
                std::cerr << "Usage: " << argv[0] << " -t <input_directory> -s <output_file> [-r 0|1]" << std::endl;
                return 1;
        }
    }

    if (inputDirectory.empty() || outputFile.empty()) {
        std::cerr << "Both -t and -s options are required." << std::endl;
        std::cerr << "Usage: " << argv[0] << " -t <input_directory> -s <output_file> [-r 0|1]" << std::endl;
        return 1;
    }

    std::cout << "Input Directory: " << inputDirectory << std::endl;
    std::cout << "Output File: " << outputFile << std::endl;

    std::string are_file = inputDirectory + "/design.are";
    std::string net_file = inputDirectory + "/design.net";
    std::string replicability_file = inputDirectory + "/design.rep";
    std::string info_filepath = inputDirectory + "/design.info";
    std::string topo_filepath = inputDirectory + "/design.topo";

    FPGAManager fpga_manager;
    fpga_manager.init(info_filepath, topo_filepath);


    read_in(are_file, net_file, replicability_file, vertex_name_to_id, vertex_id_to_name, hypergraph_init, fpga_manager);
    std::cout << "Hypergraph initialized\n";
    long long best_cost = std::numeric_limits<long long>::max();

    if(fpga_manager.fpga_num <= 32 && hypergraph_init.hypernodes.size() <= 20000){
        FullCoarsener coarsener(hypergraph_init, fpga_manager);
        std::vector<Hypergraph>& Hypergraphs_initial = coarsener.fullCoarsener(2*fpga_manager.fpga_num);
        coarsener.printCoarsenSize();
        std::cout << "Coarsening finished\n";

        int explore_num = 5;
        std::cout << "Explore " << explore_num << " times\n";

        Hypergraph hypergraph_best_overall;


        while(explore_num--){
            long long best_cost_in_this_exploration = std::numeric_limits<long long>::max();

            std::vector<Hypergraph> Hypergraphs = Hypergraphs_initial;

            int every_layer_time_limit_second = 8+explore_num;  

            int which_layer;

            std::map<int, int> best_assignment;   
            std::vector<std::vector<int>> fpgas_resource_usage; 
            std::vector<int> fpgas_comm_weight_sum; 

            AssignmentInitialPartition(Hypergraphs, fpga_manager, every_layer_time_limit_second, best_cost_in_this_exploration, best_assignment, fpgas_resource_usage, fpgas_comm_weight_sum, which_layer);

            std::cout << "Assignment Initial Partition Finished\n";

            if(best_assignment.empty()){
                continue;
            }


            updateLastLayer(Hypergraphs, which_layer, best_assignment);


            bool replication_flag = false;
            int delete1_threshold = 1;
            int delete2_threshold = 0;

            for(int i = which_layer; i >= 0; --i) {
                Hypergraphs[i].initPinsInPart();

                if(!replication_flag && (which_layer - i) < 3){
                    Exchanger exchanger(Hypergraphs[i], fpgas_resource_usage, fpgas_comm_weight_sum);
                    int exchange_gain = exchanger.exchange();
                    best_cost_in_this_exploration -= exchange_gain;
                }

                if(replication_flag){
                    Delete deleter1(Hypergraphs[i], fpga_manager, fpgas_resource_usage, fpgas_comm_weight_sum, best_cost_in_this_exploration);
                    deleter1.Solve(delete1_threshold);
                }

                {
                    Refiner move(Hypergraphs[i], fpgas_resource_usage, fpgas_comm_weight_sum);
                    int move_gain = move.refine();
                    best_cost_in_this_exploration -= move_gain;
                }

                if(replication_flag){
                    Delete deleter2(Hypergraphs[i], fpga_manager, fpgas_resource_usage, fpgas_comm_weight_sum, best_cost_in_this_exploration);
                    deleter2.Solve(delete2_threshold);
                }

                if(enableReplication){
                    int gain_threshold = (best_cost_in_this_exploration / Hypergraphs[i]._num_vertices_current) > 1 ? 1 : (best_cost_in_this_exploration / Hypergraphs[i]._num_vertices_current);
                    if(i == 0){
                        gain_threshold = 1;
                    }

                    Replication replication(Hypergraphs[i], fpga_manager, fpgas_resource_usage, fpgas_comm_weight_sum, best_cost_in_this_exploration);
                    replication.Solve(gain_threshold, replication_flag);
                }


                if(i > 0){
                    updataFormerLayer(Hypergraphs, i);
                }
            }


            
            int replication_threshold = 1;

            while(1){
                auto now_time = std::chrono::steady_clock::now();
                if(now_time - start > std::chrono::minutes(50)){
                    break;
                }

                long long new_best_cost = best_cost_in_this_exploration;

                {
                    Refiner move(Hypergraphs[0], fpgas_resource_usage, fpgas_comm_weight_sum);
                    int move_gain = move.refine();
                    new_best_cost -= move_gain;
                }

                if(replication_flag){
                    Delete deleter(Hypergraphs[0], fpga_manager, fpgas_resource_usage, fpgas_comm_weight_sum, new_best_cost);
                    deleter.Solve(0);
                }

                if(enableReplication){
                    Replication replication(Hypergraphs[0], fpga_manager, fpgas_resource_usage, fpgas_comm_weight_sum, new_best_cost);
                    replication.Solve(replication_threshold, replication_flag);
                }

                if(new_best_cost == best_cost_in_this_exploration){
                    best_cost_in_this_exploration = new_best_cost;
                    break;
                }
                best_cost_in_this_exploration = new_best_cost;
            }
            std::cout << "Refinement Finished\n";

            std::cout << "Best Cost Temp: " << best_cost_in_this_exploration << std::endl;

            if(best_cost_in_this_exploration < best_cost) {
                best_cost = best_cost_in_this_exploration;
                hypergraph_best_overall = Hypergraphs[0];
            }

            auto now_time = std::chrono::steady_clock::now();
            if(now_time - start > std::chrono::minutes(5)){
                break;
            }
        }


        std::vector<std::queue<int>> fpga_include_nodes(fpga_manager.fpga_num);

        for(auto& node : hypergraph_best_overall.hypernodes) {
            fpga_include_nodes[node.label].push(node.id);
            for(const auto& f: node.replication_fpga_labels) {
                fpga_include_nodes[f].push(-node.id-1);
            }
        }

        std::ofstream file(outputFile);
        for(int i=0;i<fpga_manager.fpga_num;++i) {
            file << fpga_manager.getName(i) << ": ";
            while(!fpga_include_nodes[i].empty()) {
                if(fpga_include_nodes[i].front() < 0) {
                    file << vertex_id_to_name[-fpga_include_nodes[i].front()-1] << "* ";
                } else {
                    file << vertex_id_to_name[fpga_include_nodes[i].front()] << " ";
                }
                fpga_include_nodes[i].pop();
            }
            file << std::endl;
        }

    }
    else {

        FullCoarsener coarsener(hypergraph_init, fpga_manager);
        std::vector<Hypergraph>& Hypergraphs = coarsener.fullCoarsener(2*fpga_manager.fpga_num);
        coarsener.printCoarsenSize();
        std::cout << "Coarsening finished\n";

        

        std::map<int, int> best_assignment;   
        std::vector<std::vector<int>> fpgas_resource_usage; 
        std::vector<int> fpgas_comm_weight_sum; 

        int every_layer_time_limit_second = 60;  

        int which_layer;

        AssignmentInitialPartition(Hypergraphs, fpga_manager, every_layer_time_limit_second, best_cost, best_assignment, fpgas_resource_usage, fpgas_comm_weight_sum, which_layer);

        std::cout << "Assignment Initial Partition Finished\n";

        updateLastLayer(Hypergraphs, which_layer, best_assignment);

        bool replication_flag = false;
        int delete1_threshold = 1;
        int delete2_threshold = 0;

        for(int i = which_layer; i >= 0; --i) {
            Hypergraphs[i].initPinsInPart();

            if(!replication_flag && (which_layer - i) < 3){
                Exchanger exchanger(Hypergraphs[i], fpgas_resource_usage, fpgas_comm_weight_sum);
                int exchange_gain = exchanger.exchange();
                best_cost -= exchange_gain;
            }

            if(replication_flag){
                Delete deleter1(Hypergraphs[i], fpga_manager, fpgas_resource_usage, fpgas_comm_weight_sum, best_cost);
                deleter1.Solve(delete1_threshold);
            }

            {
                Refiner move(Hypergraphs[i], fpgas_resource_usage, fpgas_comm_weight_sum);
                int move_gain = move.refine();
                best_cost -= move_gain;
            }

            if(replication_flag){
                Delete deleter2(Hypergraphs[i], fpga_manager, fpgas_resource_usage, fpgas_comm_weight_sum, best_cost);
                deleter2.Solve(delete2_threshold);
            }

            if(enableReplication){
                int gain_threshold = (best_cost / Hypergraphs[i]._num_vertices_current) > 1 ? 1 : (best_cost / Hypergraphs[i]._num_vertices_current);
                if(i == 0){
                    gain_threshold = 1;
                }

                Replication replication(Hypergraphs[i], fpga_manager, fpgas_resource_usage, fpgas_comm_weight_sum, best_cost);
                replication.Solve(gain_threshold, replication_flag);
            }


            if(i > 0){
                updataFormerLayer(Hypergraphs, i);
            }
        }

        
        int replication_threshold = 1;

        while(1){
            auto now_time = std::chrono::steady_clock::now();
            if(now_time - start > std::chrono::minutes(50)){
                break;
            }

            long long new_best_cost = best_cost;

            {
                Refiner move(Hypergraphs[0], fpgas_resource_usage, fpgas_comm_weight_sum);
                int move_gain = move.refine();
                new_best_cost -= move_gain;
            }

            if(replication_flag){
                Delete deleter(Hypergraphs[0], fpga_manager, fpgas_resource_usage, fpgas_comm_weight_sum, new_best_cost);
                deleter.Solve(0);
            }

            if(enableReplication){
                Replication replication(Hypergraphs[0], fpga_manager, fpgas_resource_usage, fpgas_comm_weight_sum, new_best_cost);
                replication.Solve(replication_threshold, replication_flag);
            }

            if(new_best_cost == best_cost){
                best_cost = new_best_cost;
                break;
            }
            best_cost = new_best_cost;
        }
        std::cout << "Refinement Finished\n";

        std::vector<std::queue<int>> fpga_include_nodes(fpga_manager.fpga_num);

        for(auto& node : Hypergraphs[0].hypernodes) {
            fpga_include_nodes[node.label].push(node.id);
            for(const auto& f: node.replication_fpga_labels) {
                fpga_include_nodes[f].push(-node.id-1);
            }
        }

        std::ofstream file(outputFile);
        for(int i=0;i<fpga_manager.fpga_num;++i) {
            file << fpga_manager.getName(i) << ": ";
            while(!fpga_include_nodes[i].empty()) {
                if(fpga_include_nodes[i].front() < 0) {
                    file << vertex_id_to_name[-fpga_include_nodes[i].front()-1] << "* ";
                } else {
                    file << vertex_id_to_name[fpga_include_nodes[i].front()] << " ";
                }
                fpga_include_nodes[i].pop();
            }
            file << std::endl;
        }
    }

    auto end_time = std::chrono::steady_clock::now();

    std::cout << "Total Time: " << std::chrono::duration_cast<std::chrono::minutes>(end_time - start).count() << "min = " <<
    std::chrono::duration_cast<std::chrono::seconds>(end_time - start).count() << "s = " <<
    std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start).count() << "ms" << std::endl;

    std::cout << "Best Cost (Total Hop Distance Cost): " << best_cost << std::endl;

    return 0;
}
