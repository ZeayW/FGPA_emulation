# Makefile

# 如果用户没有指定 c 和 o 参数，默认输出格式提示
ifndef c
    $(error "Usage: make c=<case_number> s=<source_file.cpp> o=<output_file_suffix>")
endif

ifndef o
    $(error "Usage: make c=<case_number> s=<source_file.cpp> o=<output_file_suffix>")
endif

# 获取可执行文件名称（仅在需要编译时检查 s 参数）
ifdef s
    EXE_NAME = $(basename $(s))
endif

# 将 c 填充为两位数
CASE_NUM_PADDED = $(shell printf "%02d" $(c))

# 根据 c 生成 TEST_PATH
ifeq ($(c),0)
    TEST_PATH = ../testcase/sample01
else
    TEST_PATH = ../testcase/case$(CASE_NUM_PADDED)
endif

# 自动补全 OUT_PATH
ifeq ($(c),0)
    OUT_PATH = ../testcase/sample01/out/design.fpga.out_$(o)
else
    OUT_PATH = ../testcase/case$(CASE_NUM_PADDED)/out/design.fpga.out_$(o)
endif

# 默认目标，清理并重新编译和运行
all: clean $(EXE_NAME)
	./$(EXE_NAME) -t $(TEST_PATH) -s $(OUT_PATH)

# 编译规则，生成的可执行文件名与 .cpp 文件名相同
$(EXE_NAME): $(s)
	g++ -Ofast -DNDEBUG -o $(EXE_NAME) $(s) \
    -I../boost_1_86_0/include \
    -L../boost_1_86_0/lib \
    -static -lboost_thread -lboost_system -pthread

# test 目标，运行指定的评估命令，自动使用完整的 OUT_PATH
test:
	../evaluator/evaluator -t $(TEST_PATH) -s $(OUT_PATH)

# 清理规则
clean:
	rm -f $(EXE_NAME)

# 防止误识别 make 命令作为文件，确保每次运行都重新编译
.PHONY: all clean test $(EXE_NAME)
