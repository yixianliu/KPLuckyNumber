/*
 Navicat Premium Dump SQL

 Source Server         : 本地MySql服务器
 Source Server Type    : MySQL
 Source Server Version : 80012 (8.0.12)
 Source Host           : localhost:3306
 Source Schema         : lucky_number

 Target Server Type    : MySQL
 Target Server Version : 80012 (8.0.12)
 File Encoding         : 65001

 Date: 10/06/2026 11:42:18
*/

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- Table structure for qxc_detailed_report
-- ----------------------------
DROP TABLE IF EXISTS `qxc_detailed_report`;
CREATE TABLE `qxc_detailed_report`  (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `report_date` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '报告日期',
  `report_uuid` varchar(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '报告唯一标识',
  `raw_data_snapshot` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '原始数据快照',
  `calculation_steps` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '计算步骤记录',
  `analysis_params` text CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '分析参数配置',
  `frequency_analysis` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '频率分析结果',
  `probability_analysis` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '概率分析结果',
  `interval_analysis` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '间隔分析结果',
  `hezhi_analysis` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '和值分析结果',
  `odd_even_analysis` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '奇偶分析结果',
  `span_analysis` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '跨度分析结果',
  `total_samples` int(11) NULL DEFAULT NULL COMMENT '分析样本数',
  `confidence_level` decimal(5, 2) NULL DEFAULT NULL COMMENT '置信水平',
  `report_content` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '报告内容',
  `frequency_chart` longblob NULL COMMENT '频率分布图',
  `probability_chart` longblob NULL COMMENT '概率分布图',
  `created_at` timestamp NULL DEFAULT NULL COMMENT '创建时间',
  `updated_at` timestamp NULL DEFAULT NULL COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `report_uuid`(`report_uuid` ASC) USING BTREE,
  INDEX `idx_report_date`(`report_date` ASC) USING BTREE,
  INDEX `idx_report_uuid`(`report_uuid` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 3 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '七星彩详细分析报告表' ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for qxc_final_report
-- ----------------------------
DROP TABLE IF EXISTS `qxc_final_report`;
CREATE TABLE `qxc_final_report`  (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `detailed_report_id` int(11) NULL DEFAULT NULL COMMENT '关联详细报告ID',
  `report_date` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '报告日期',
  `report_uuid` varchar(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '报告唯一标识',
  `recommended_numbers` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '推荐号码组合',
  `confidence_score` decimal(5, 2) NULL DEFAULT NULL COMMENT '置信分数',
  `analysis_summary` text CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '分析摘要',
  `key_conclusions` text CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '关键结论',
  `core_metrics` text CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '核心指标',
  `decision_recommendations` text CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '决策建议',
  `report_content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '报告内容',
  `frequency_chart` longblob NULL COMMENT '频率分布图',
  `probability_chart` longblob NULL COMMENT '概率分布图',
  `status` enum('draft','validated','published') CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '报告状态',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `report_uuid`(`report_uuid` ASC) USING BTREE,
  INDEX `idx_final_report_date`(`report_date` ASC) USING BTREE,
  INDEX `idx_final_report_uuid`(`report_uuid` ASC) USING BTREE,
  INDEX `idx_detailed_report_id`(`detailed_report_id` ASC) USING BTREE,
  CONSTRAINT `fk_detailed_report` FOREIGN KEY (`detailed_report_id`) REFERENCES `qxc_detailed_report` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 3 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '七星彩最终最优报告表' ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for qxc_history_data
-- ----------------------------
DROP TABLE IF EXISTS `qxc_history_data`;
CREATE TABLE `qxc_history_data`  (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `issue` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '期号（唯一标识）',
  `draw_date` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '开奖日期',
  `num1` int(11) NULL DEFAULT NULL COMMENT '第一位号码',
  `num2` int(11) NULL DEFAULT NULL COMMENT '第二位号码',
  `num3` int(11) NULL DEFAULT NULL COMMENT '第三位号码',
  `num4` int(11) NULL DEFAULT NULL COMMENT '第四位号码',
  `num5` int(11) NULL DEFAULT NULL COMMENT '第五位号码',
  `num6` int(11) NULL DEFAULT NULL COMMENT '第六位号码',
  `special_num` int(11) NULL DEFAULT NULL COMMENT '特别号码',
  `hezhi` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '和值',
  `hezhi_type` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '和值类型（奇偶）',
  `odd_even_ratio` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '奇偶比例',
  `odd_even_pattern` text CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '奇偶模式',
  `span` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '跨度',
  `created_at` timestamp NULL DEFAULT NULL COMMENT '创建时间',
  `updated_at` timestamp NULL DEFAULT NULL COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `issue`(`issue` ASC) USING BTREE,
  INDEX `idx_issue`(`issue` ASC) USING BTREE,
  INDEX `idx_draw_date`(`draw_date` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 121 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '七星彩历史开奖数据表' ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for qxc_trend_data
-- ----------------------------
DROP TABLE IF EXISTS `qxc_trend_data`;
CREATE TABLE `qxc_trend_data`  (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `issue` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL DEFAULT NULL COMMENT '期号（关联开奖数据）',
  `trend_values` text CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '走势图数据JSON',
  `created_at` timestamp NULL DEFAULT NULL COMMENT '创建时间',
  `updated_at` timestamp NULL DEFAULT NULL COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `issue`(`issue` ASC) USING BTREE,
  INDEX `idx_issue`(`issue` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 121 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '七星彩走势图数据表' ROW_FORMAT = Dynamic;

SET FOREIGN_KEY_CHECKS = 1;
