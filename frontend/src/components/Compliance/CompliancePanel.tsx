import React, { useState } from 'react';
import { useDocumentStore, type ComplianceItem } from '../../stores/documentStore';
import './CompliancePanel.css';

interface CompliancePanelProps {
    className?: string;
}

export const CompliancePanel: React.FC<CompliancePanelProps> = ({ className }) => {
    const {
        currentDocument,
        config,
        setHighlights,
        setCurrentPage
    } = useDocumentStore();

    const currentDocumentId = currentDocument?.id;
    const apiBaseUrl = config.apiBaseUrl || 'http://localhost:8000';

    const [requirements, setRequirements] = useState('');
    const [results, setResults] = useState<ComplianceItem[]>([]);
    const [loading, setLoading] = useState(false);

    const handleCheck = async () => {
        if (!currentDocumentId || !requirements.trim()) return;

        setLoading(true);
        try {
            const reqList = requirements.split('\n').filter(r => r.trim());

            const response = await fetch(`${apiBaseUrl}/api/documents/${currentDocumentId}/compliance`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    requirements: reqList,
                    api_key: config.deepseekApiKey || config.zhipuApiKey
                }),
            });

            if (!response.ok) throw new Error('Check failed');

            const data = await response.json();
            setResults(data);
        } catch (error) {
            console.error(error);
            alert('检查失败，请重试');
        } finally {
            setLoading(false);
        }
    };

    const handleRefClick = (ref: any) => {
        setHighlights([ref]);
        setCurrentPage(ref.page || ref.page_number);
    };

    const getStatusColor = (status: string) => {
        switch (status) {
            case 'satisfied': return 'bg-green-100 text-green-800';
            case 'unsatisfied': return 'bg-red-100 text-red-800';
            case 'partial': return 'bg-yellow-100 text-yellow-800';
            default: return 'bg-gray-100 text-gray-800';
        }
    };

    const getStatusText = (status: string) => {
        switch (status) {
            case 'satisfied': return '符合';
            case 'unsatisfied': return '不符合';
            case 'partial': return '部分符合';
            case 'unknown': return '未知';
            case 'error': return '错误';
            default: return status;
        }
    };

    return (
        <div className={`compliance-panel ${className || ''}`}>
            <div className="input-section">
                <textarea
                    className="req-input"
                    placeholder="请输入技术要求，每行一条..."
                    value={requirements}
                    onChange={(e) => setRequirements(e.target.value)}
                    disabled={loading}
                />
                <button
                    className="check-btn"
                    onClick={handleCheck}
                    disabled={loading || !currentDocumentId}
                >
                    {loading ? '正在检查...' : '开始合规性检查'}
                </button>
            </div>

            <div className="results-section">
                <table className="compliance-table">
                    <thead>
                        <tr>
                            <th style={{ width: '60px' }}>序号</th>
                            <th style={{ width: '30%' }}>技术要求</th>
                            <th>合规情况说明</th>
                            <th style={{ width: '100px' }}>状态</th>
                        </tr>
                    </thead>
                    <tbody>
                        {results.map((item) => (
                            <tr key={item.id}>
                                <td className="text-center">{item.id}</td>
                                <td>{item.requirement}</td>
                                <td>
                                    <div className="response-text">{item.response}</div>
                                    {item.references && item.references.length > 0 && (
                                        <div className="refs-container">
                                            {item.references.map((ref: any, idx) => {
                                                const pageNum = ref.page_number || ref.page;
                                                return (
                                                    <span
                                                        key={idx}
                                                        className="ref-tag"
                                                        onClick={() => {
                                                            // Ensure standard format for store
                                                            const standardRef = { ...ref, page: pageNum };
                                                            handleRefClick(standardRef);
                                                        }}
                                                        title={`第 ${pageNum} 页`}
                                                    >
                                                        {pageNum}页
                                                    </span>
                                                );
                                            })}
                                        </div>
                                    )}
                                </td>
                                <td>
                                    <span className={`status-badge ${getStatusColor(item.status)}`}>
                                        {getStatusText(item.status)}
                                    </span>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
};
