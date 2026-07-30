# RePart
## Hypergraph Partitioning for Multi-FPGA Systems
Implementation of **RePart: Efficient Hypergraph Partitioning with Logic Replication Optimization for Multi-FPGA System**.
An advanced hypergraph partitioning implementation designed for deploying circuit hypergraphs onto multi-FPGA systems. Features:

- Logic replication for communication minimization
- Algorithmic enhancements for partitioning quality in coarsening, assignment and refinement phases of the partitioning algorithm
- End-to-end implementation considering FPGA-specific constraints

Some data structure implementations are derived from [KaHyPar](https://github.com/kahypar/kahypar?tab=GPL-3.0-2-ov-file) .

## Datasets Preparation
### 1. Titan23 Benchmark
- We use Titan23 benchmark dataset from [Timing Driven Titan: Enabling Large Benchmarks and Exploring the Gap Between Academic and Commercial CAD]. The hypergraphs of them can be found from [Google Drive](https://drive.google.com/drive/folders/14cXR0dZA-3H5BY0BcZ6KFvDf4E1NeTon?usp=sharing.) provided by TopoPart, which can also be found in the `./TiTan23\ Benchmarks` folder. We can use `generateDataset.py` to convert origin format datasets to [Integrated Circuit EDA Elite Challenge Contest](https://eda.icisc.cn/en/index) Problem 3 format.

- The MFS56 is "synopsys02" from problem B in [ICCAD 2019 Contest](https://www.iccad-contest.org/2019/) 

``` bash
$ python generateDataset.py 'Input Dataset Folder Path' 'FPGA(MFS) File Path' 'Output Dataset Folder Path'
```
For example
``` bash
$ python ./generateDataset.py ./Titan23\ Benchmarks/ ./MFS56 ./Titan23_Transformed
```
The imbalance factor $\epsilon$ and max-hop-distance constraints should be set in case constraints in our format. You can change $\epsilon$ and max-hop-distance constraint directly in `generateDataset.py`
``` python
balance_factor = 0.2
hop_max = 5 # max hop-distance (hop count + 1) of a legal edge
```
### 2. Integrated Circuit EDA Elite Challenge Contest
10 cases from [Integrated Circuit EDA Elite Challenge Contest](https://eda.icisc.cn/en/index) Problem 3.

Preloaded test cases are in `./testcase`. 

## Compilation
``` bash
$ cd ./RePart
$ g++ -Ofast -DNDEBUG -o partitioner partitioner.cpp \
    -I../boost_1_86_0/include \
    -L../boost_1_86_0/lib \
    -static -lboost_thread -lboost_system -pthread
```
## Run RePart
### Titan23 Benchmark
``` bash
$ ./partitioner -t ../Titan23_Transformed/neuron/ -s ../Titan23_Transformed/neuron/design.fpga.out
```
Note: Replace `Titan23_Transformed` with your output folder name.

Output results are saved to `Titan23_Transformed/neuron/design.fpga.out`

### Integrated Circuit EDA Elite Challenge Contest
Example (Case03):
``` bash
$ ./partitioner -t ../testcase/case03 -s ../testcase/case03/design.fpga.out
```
Output results are saved to `testcase/case03/design.fpga.out`.



## Citation

If you use **RePart** in your research or project, please cite our paper:

```bibtex
@misc{fu2026repartefficienthypergraphpartitioning,
      title={RePart: Efficient Hypergraph Partitioning with Logic Replication Optimization for Multi-FPGA System},
      author={Zizhuo Fu and Yifan Zhou and Zhaoxin Lu and Guangyu Sun and Runsheng Wang and Meng Li and Yibo Lin},
      year={2026},
      eprint={2604.00780},
      archivePrefix={arXiv},
      primaryClass={cs.AR},
      url={https://arxiv.org/abs/2604.00780},
}
```
